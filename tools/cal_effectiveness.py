"""D5 校正有效性：同一条相容观测，真系数恢复输入、名义系数留下失配误差。

关键：观测由**真权重**经生产链自己算出的 dac_hw 生成，
A/B 两侧喂**完全相同的** adc2/dither/码，只换系数。
不得硬编码 slice ID —— 一律取 run_transaction 返回的实际值。
"""

from __future__ import annotations

import numpy as np
from adi_model.digital_chain import run_transaction, Transaction, _dither_terms, N_COLUMNS
Q=1<<30; rng=np.random.default_rng(2026)
def make_instance():
    w={}
    for sid in range(18):
        g=1.0+rng.normal(0,0.01)
        for j in range(N_COLUMNS): w[(sid,j)]=0.25*g*(1+rng.normal(0,0.015))
        for i,wt in enumerate((0.5,0.25,0.125,0.0625)): w[(sid,16+i)]=(wt/8)*g*(1+rng.normal(0,0.025))
    return {k:int(round(v*Q)) for k,v in w.items()}
wt=make_instance()
nom={}
for s in range(18):
    for j in range(N_COLUMNS): nom[(s,j)]=int(0.25*Q)
    for i,w2 in enumerate((0.5,0.25,0.125,0.0625)): nom[(s,16+i)]=int(w2*Q/8)

eA=[];eB=[];rec=[]
for _ in range(300):
    sid=int(rng.integers(0,300)); k=int(rng.integers(0,512)); word=int(rng.integers(0,64))
    u,l,t=_dither_terms(word)
    if k+u>511: continue
    # 先用 u=0 探针拿生产链自己算的 dac（真权重）
    p=run_transaction(Transaction(sid,k+u,word,0,wt))
    if p.get('dac_hw_q24') is None: continue
    d=p['dac_hw_q24']; dT=int(round(t*196608))
    xq=d+dT+int(rng.integers(-200000,200000))
    x=xq/2**24
    r=xq-d-dT
    a=int(round((r*5/144-1)/2))
    if not (0<=a<=4095): continue
    ra=run_transaction(Transaction(sid,k+u,word,a,nom))
    rb=run_transaction(Transaction(sid,k+u,word,a,wt))
    if ra.get('x_weight_v') is None or rb.get('x_weight_v') is None: continue
    eA.append(abs(ra['x_weight_v']-x)); eB.append(abs(rb['x_weight_v']-x))
    if len(rec)<3: rec.append((sid,p['slice_ids'],p['wm_q30'],p['g_q30'],p['inv_g_q60'],a,rb['code20'],ra['code20']))
rmsA=np.sqrt(np.mean(np.square(eA))); rmsB=np.sqrt(np.mean(np.square(eB)))
assert rmsB < 1e-5, f"真系数 rms 过大: {rmsB*1e6:.1f} uV"
assert rmsA > 100*rmsB, f"名义系数没有明显劣化: {rmsA*1e6:.1f} vs {rmsB*1e6:.1f} uV"
print("A 名义 n=%d max=%.1f uV rms=%.1f uV"%(len(eA),max(eA)*1e6,np.sqrt(np.mean(np.square(eA)))*1e6))
print("B 真系数 n=%d max=%.1f uV rms=%.1f uV"%(len(eB),max(eB)*1e6,np.sqrt(np.mean(np.square(eB)))*1e6))
print("改善倍数 rms: %.1fx  max: %.1fx"%(np.sqrt(np.mean(np.square(eA)))/np.sqrt(np.mean(np.square(eB))), max(eA)/max(eB)))
for r in rec: print("事务记录 sid=%d ids=%s Wm=%d G=%d inv=%d adc=%d codeB=%d codeA=%d"%r)
