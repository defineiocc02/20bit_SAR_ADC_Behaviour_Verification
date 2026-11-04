#!/usr/bin/env python3
"""Read-only production-model audit. All outputs belong to the independent audit.

Metrics are independently defined here; estimation uses the production estimator
and only its whitelisted digital record plus the declared external reference.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import time
from dataclasses import asdict, replace
from pathlib import Path
import sys
import numpy as np


def dump(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def metrics(y, x, fs, fin, vfs):
    y, x = np.asarray(y), np.asarray(x)
    e = y-x
    n = len(y)
    fft = np.fft.rfft(y-y.mean())
    power = np.abs(fft/n)**2
    power[1:-1] *= 2
    k = int(round(fin*n/fs))
    fundamental = power[k]
    mask = np.ones(len(power), bool); mask[0] = mask[k] = False
    nd = power[mask].sum()
    harmonics = set()
    for h in range(2, 11):
        kh = (h*k) % n
        kh = min(kh, n-kh)
        if kh not in (0,k): harmonics.add(kh)
    distortion = float(sum(power[kh] for kh in harmonics))
    noise = max(float(nd-distortion), np.finfo(float).tiny)
    t=np.arange(n)/fs
    basis=np.column_stack((np.ones(n),np.sin(2*np.pi*fin*t),np.cos(2*np.pi*fin*t)))
    fitted=basis@np.linalg.lstsq(basis,y,rcond=None)[0]
    sndr=float(10*np.log10(fundamental/nd))
    return {
        "samples":n,"error_mean_v":float(e.mean()),"error_rms_v":float(np.sqrt(np.mean(e*e))),
        "error_std_v":float(e.std()),"error_max_abs_v":float(abs(e).max()),
        "dc_fundamental_removed_rms_v":float(np.sqrt(np.mean((y-fitted)**2))),
        "SNDR_dB":sndr,"SNR_dB_10_harmonics_excluded":float(10*np.log10(fundamental/noise)),
        "SFDR_dBc":float(10*np.log10(fundamental/power[mask].max())),
        "THD_dB_2_to_10":float(10*np.log10(max(distortion,np.finfo(float).tiny)/fundamental)),
        "ENOB_bits":(sndr-1.76)/6.02,"fundamental_bin":k,
        "coherence_error_cycles":float(fin*n/fs-k),
        "fft_contract":{"window":"rectangular","window_power_correction":1,"dc":"excluded",
          "fundamental":"single coherent bin", "harmonics":"included in SNDR; orders 2..10 excluded only in reported SNR",
          "power_normalization":"one-sided |FFT/N|^2; interior bins doubled", "plot_unit":"dBFS/bin relative to vfs^2/2",
          "analysis_bandwidth_hz":fs/2,"full_scale_sine_peak_v":vfs,"sample_rate_hz":fs,"tone_hz":fin}}


def save_capture(path, result, data, known=None, **extra):
    payload={
        "slice_ids":data.slice_ids,"rdac_code":data.rdac_code,"dem_state":data.dem_state,
        "adc2_code":data.adc2_code,"bank_dither":data.bank_dither,
        "common_injection_v":data.common_injection_v,"overflow":data.overflow,
        "sample_id":result.sample_id,"t_s":result.sample.t1,"x_clean_v":result.sample.x1_clean,
        "residue_v":result.residue,"ra_output_v":result.vra,
        "adc2_input_v":result.adc2_input_voltage,"ra_sat":result.ra_sat,"adc2_over":result.adc2_over,
        "sadc_coarse":result.coarse,"dither_v":result.sample.dither,
    }
    if known is not None:payload["external_known_training_input_v"]=known
    payload.update(extra)
    np.savez_compressed(path,**payload)


def calibration_run(args):
    from adi_model import Config, sine_input
    from adi_model.pipeline import run_pipeline
    from adi_model.slice_pool import PhysicalSlicePool
    from adi_model.weight_calibration import DigitalObservation, fit_unit_weights, FrozenCalibration
    from adi_model.fixed_point import FixedPointReconstructor
    from adi_model.scheduler import ShuffledScheduler
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    cfg=Config.paper_literal(fs=args.fs,seed=args.seed,dem_enable=True,dem_mode="permute",
      dither_mode="off",mismatch_sigma0=args.mismatch,sadc_rdac_gain_mismatch=0,
      enable_sampling_noise=args.noise,ra_enable_noise=args.noise,target_dr_db=args.target_dr)
    train_cfg=replace(cfg,dither_mode="sampling",dither_split_bank="sub",dither_discrete=True,dither_units_range=2)
    pool=PhysicalSlicePool(cfg,np.random.default_rng(cfg.seed+8101))
    train_fn=sine_input(2.4,cfg.fs*103/args.ncal)
    started=time.monotonic()
    train=run_pipeline(train_cfg,train_fn,args.ncal,pool=pool,
      rng=np.random.default_rng(cfg.seed+19001),
      scheduler=ShuffledScheduler(train_cfg,np.random.default_rng(cfg.seed+19003)))
    td=DigitalObservation.from_result(train)
    known=np.asarray(train_fn(train.sample.t1))
    save_capture(out/"training.npz",train,td,known)
    dump(out/"config.json",{"training":train_cfg.to_dict(),"validation":cfg.to_dict(),
      "training_reference":"2.4 V peak coherent sine at 103*fs/ncal; exact externally declared waveform",
      "noise_seed_training":cfg.seed+19001,"scheduler_seed_training":cfg.seed+19003,
      "fabrication_seed":cfg.seed+8101,"ncal":args.ncal,"nval":args.nval})
    fit=fit_unit_weights(td,known)
    dump(out/"coefficients.json",fit.to_dict())
    fit=FrozenCalibration.from_dict(json.loads((out/"coefficients.json").read_text()))
    frozen_digest=hashlib.sha256(fit.weights.tobytes()).hexdigest()
    n=args.nval;fin=cfg.fs*args.cycles/n
    val_fn=sine_input(args.amplitude,fin,0.5)
    val=run_pipeline(cfg,val_fn,n,pool=pool,rng=np.random.default_rng(cfg.seed+19002))
    vd=DigitalObservation.from_result(val)
    before=FixedPointReconstructor.from_result(val).reconstruct(vd)
    fp=FixedPointReconstructor.from_weights(vd.spec,fit.weights,fit.offset_v)
    after=fp.reconstruct(vd)
    float_after=fit.reconstruct(vd)
    dump(out/"fixed_registers.json",fp.to_dict())
    save_capture(out/"validation.npz",val,vd,before_code=before.code,after_code=after.code,
      before_v=before.voltage,after_v=after.voltage,float_before_v=val.out,float_after_v=float_after)
    result={"source_layer":"production Python behavioral model; not HDL or transistor simulation",
      "fit":{"rank":fit.rank,"unknowns":cfg.n_slices*(cfg.dac_n_main+cfg.dac_n_sub)+1,
        "condition":fit.condition,"training_residual_rms_v":fit.residual_rms_v,"training_digest":fit.training_digest},
      "same_physical_pool":train.pool is val.pool,"training_validation_separate":True,
      "coefficients_unchanged":frozen_digest==hashlib.sha256(fit.weights.tobytes()).hexdigest(),
      "validation":{"fs_hz":cfg.fs,"fin_hz":fin,"amplitude_v":args.amplitude,"phase_rad":0.5,
        "n":n,"seed":cfg.seed,"noise_enabled":args.noise,"mismatch_sigma":args.mismatch,
        "target_dr_noise_budget_db":cfg.target_dr_db},
      "before_integer":metrics(before.voltage,val.sample.x1_clean,cfg.fs,fin,cfg.v_fs),
      "after_integer":metrics(after.voltage,val.sample.x1_clean,cfg.fs,fin,cfg.v_fs),
      "before_float":metrics(val.out,val.sample.x1_clean,cfg.fs,fin,cfg.v_fs),
      "after_float":metrics(float_after,val.sample.x1_clean,cfg.fs,fin,cfg.v_fs),
      "analog_overflow_count":int(vd.overflow.sum()),"ra_sat_count":int(val.ra_sat.sum()),
      "output_clip_count_before":int((before.clipped_low|before.clipped_high).sum()),
      "output_clip_count_after":int((after.clipped_low|after.clipped_high).sum()),
      "peak_accumulator_bits":after.peak_accumulator_bits,"elapsed_s":time.monotonic()-started}
    if args.sweep:
        rows=[]
        for amplitude in (0.3,2.2,2.85):
            for cycles in (31,307,1853):
                freq=cfg.fs*cycles/4096
                r=run_pipeline(cfg,sine_input(amplitude,freq,0.5),4096,pool=pool,
                    rng=np.random.default_rng(cfg.seed+19002+cycles))
                d=DigitalObservation.from_result(r)
                b=FixedPointReconstructor.from_result(r).reconstruct(d)
                a=FixedPointReconstructor.from_weights(d.spec,fit.weights,fit.offset_v).reconstruct(d)
                row={"amplitude_v":amplitude,"fin_hz":freq,"cycles":cycles,"seed":cfg.seed,
                  "noise_seed":cfg.seed+19002+cycles,"overflow_count":int(d.overflow.sum()),
                  "before_integer":metrics(b.voltage,r.sample.x1_clean,cfg.fs,freq,cfg.v_fs),
                  "after_integer":metrics(a.voltage,r.sample.x1_clean,cfg.fs,freq,cfg.v_fs)}
                rows.append(row)
                np.savez_compressed(out/f"sweep_A{amplitude}_k{cycles}.npz",x_v=r.sample.x1_clean,
                    before_code=b.code,after_code=a.code,overflow=d.overflow)
        result["sweep"]=rows
    dump(out/"metrics.json",result)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)


def dynamics_run(args):
    from adi_model.conversion import response_interval
    from scipy.integrate import solve_ivp
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    duration=40e-9;t=np.linspace(0,duration,401);tau=3e-9;target=0.25;drive=-0.03
    b=2*np.pi*120e6;wide=2*np.pi*150e6;narrow=2*np.pi*15e6;switch=0.65*duration
    def oracle_rhs(time_s,state):
        c=wide if time_s<switch else narrow
        return [b*(target+drive*np.exp(-time_s/tau)-state[0]),c*(state[0]-state[1])]
    left=solve_ivp(oracle_rhs,(0,switch),[-0.1,0.12],method="DOP853",rtol=1e-12,atol=1e-14,dense_output=True,max_step=duration/100)
    right=solve_ivp(oracle_rhs,(switch,duration),left.y[:,-1],method="DOP853",rtol=1e-12,atol=1e-14,dense_output=True,max_step=duration/100)
    oracle=np.array([(left.sol(u) if u<=switch else right.sol(u)) for u in t])
    model=[]
    for u in t:
        if u==0:model.append((-0.1,0.12));continue
        first=min(u,switch)
        y,z,_=response_interval(-0.1,0.12,target,drive,tau,first,ra_bw_hz=b/(2*np.pi),adc_bw_hz=wide/(2*np.pi),clip_v=1)
        if u>switch:y,z,_=response_interval(y,z,target,drive*np.exp(-switch/tau),tau,u-switch,ra_bw_hz=b/(2*np.pi),adc_bw_hz=narrow/(2*np.pi),clip_v=1)
        model.append((y,z))
    model=np.asarray(model)
    np.savez_compressed(out/"settling.npz",t_s=t,model_ra_v=model[:,0],model_adc2_v=model[:,1],oracle_ra_v=oracle[:,0],oracle_adc2_v=oracle[:,1],reference_drive_v=drive*np.exp(-t/tau),target_v=np.full(t.shape,target))
    result={"evidence":"production response_interval vs independent scipy DOP853 ODE; assumed circuit parameters",
      "sample_rate_hz":10e6,"RA_interval_s":duration,"RA_gain":32,"reference_tau_s":tau,
      "RA_bandwidth_hz":b/(2*np.pi),"ADC2_wide_bandwidth_hz":wide/(2*np.pi),
      "ADC2_narrow_bandwidth_hz":narrow/(2*np.pi),"switch_s":switch,
      "max_model_vs_ode_v":float(abs(model-oracle).max()),"RA_deadline_error_input_referred_v":float((model[-1,0]-target)/32),
      "ADC2_deadline_error_input_referred_v":float((model[-1,1]-target)/32),
      "reference_endpoint_v":float(drive*np.exp(-duration/tau)),"noise_benefit":"NOT_EVALUATED: signal-only ODE"}
    dump(out/"metrics.json",result);print(json.dumps(result),flush=True)


def dither_run(args):
    from adi_model import Config, sine_input
    from adi_model.pipeline import run_pipeline
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    rows=[]
    n=8192;cycles=307
    for amplitude in (2.2,2.99):
        for mode in ("off","analog","sampling","quantizer"):
            cfg=Config.paper_literal(fs=args.fs,seed=args.seed,mismatch_enable=False,
              sadc_rdac_gain_mismatch=0,enable_sampling_noise=False,ra_enable_noise=False,
              dem_enable=True,dither_mode=mode,dither_discrete=True)
            r=run_pipeline(cfg,sine_input(amplitude,cfg.fs*cycles/n,0.5),n)
            if mode=="off":corr=np.zeros(n)
            elif mode=="sampling":corr=r.sample.dither_code*cfg.nominal_rdac_step
            elif mode=="quantizer":corr=r.sample.rdac_dither
            else:corr=r.sample.dither
            omitted=(r.vd0+r.fine/cfg.g0)/cfg.dither_alpha
            # Independent ideal algebra: physical stored input minus nominal DAC
            # gives residue; backend quantization is the only accepted ideal error.
            residue_expected=r.sample.x_rdac-r.vd0
            physical_reference=-cfg.v_fs+cfg.nominal_rdac_step*np.clip(r.k,0,2**cfg.b1-1)
            residue_physical_expected=r.sample.x_rdac-physical_reference
            integer=r.to_codes()
            key=f"A{amplitude}_{mode}"
            np.savez_compressed(out/f"{key}.npz",x_v=r.sample.x1_clean,
              dither_v=r.sample.dither,rdac_dither_v=(np.zeros(n) if r.sample.rdac_dither is None else r.sample.rdac_dither),
              correction_v=corr,code=r.k,residue_v=r.residue,ra_v=r.vra,adc2_v=r.adc2_input_voltage,
              adc2_over=r.adc2_over,ra_sat=r.ra_sat,rdac_over=r.rdac_over,
              output_v=r.out,integer_output_code=integer.code,integer_output_v=integer.voltage,
              integer_clip_low=integer.clipped_low,integer_clip_high=integer.clipped_high,
              independent_physical_dac_v=physical_reference,omitted_compensation_v=omitted)
            row={"key":key,"mode":mode,"amplitude_v":amplitude,"fs_hz":cfg.fs,"n":n,"seed":cfg.seed,
              "noise":False,"mismatch":False,"config":cfg.to_dict(),
              "residue_min_v":float(r.residue.min()),"residue_max_v":float(r.residue.max()),
              "RA_min_v":float(r.vra.min()),"RA_max_v":float(r.vra.max()),
              "RA_window_v":[-cfg.ra_v_clip,cfg.ra_v_clip],"ADC2_window_v":[cfg.adc2_v_min,cfg.adc2_v_max],
              "ra_sat_count":int(r.ra_sat.sum()),"adc2_over_count":int(r.adc2_over.sum()),"rdac_over_count":int(r.rdac_over.sum()),
              "unclipped_nominal_projection_difference_v":float(abs(r.residue-residue_expected).max()),
              "independent_clipped_physical_charge_max_abs_v":float(abs(r.residue-residue_physical_expected).max()),
              "samples_retained":n,"samples_filtered":0,
              "integer_analog_overflow_count":int(integer.analog_overflow.sum()),
              "integer_output_clip_count":int((integer.clipped_low|integer.clipped_high).sum()),
              "integer_error_rms_v":float(np.sqrt(np.mean((integer.voltage-r.sample.x1_clean)**2))),
              "corrected_error_rms_v":float(np.sqrt(np.mean((r.out-r.sample.x1_clean)**2))),
              "omitted_compensation_error_rms_v":float(np.sqrt(np.mean((omitted-r.sample.x1_clean)**2))),
              "nominal_signal_alpha":cfg.dither_alpha,"nominal_digital_correction_peak_v":float(abs(corr).max())}
            rows.append(row)
    dump(out/"metrics.json",rows);print(json.dumps(rows),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument("mode",choices=["calibration","dynamics","dither"])
    p.add_argument("--repo",required=True);p.add_argument("--out",required=True)
    p.add_argument("--fs",type=float,default=10e6);p.add_argument("--seed",type=int,default=42)
    p.add_argument("--ncal",type=int,default=16384);p.add_argument("--nval",type=int,default=16384)
    p.add_argument("--cycles",type=int,default=307);p.add_argument("--amplitude",type=float,default=2.2)
    p.add_argument("--mismatch",type=float,default=0.001);p.add_argument("--noise",action="store_true")
    p.add_argument("--target-dr",type=float,default=94.6)
    p.add_argument("--sweep",action="store_true");args=p.parse_args()
    sys.path.insert(0,str(Path(args.repo)/"src"))
    if args.mode=="calibration":calibration_run(args)
    elif args.mode=="dynamics":dynamics_run(args)
    else:dither_run(args)

if __name__=="__main__":main()
