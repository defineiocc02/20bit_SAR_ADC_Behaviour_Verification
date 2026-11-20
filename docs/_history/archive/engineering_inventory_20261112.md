# 工程清点（E0）

受测版本 `9beca9f`。按职责包列出每个模块的规模与公开面，供迁移/合并决策使用。

**处置说明**：本轮实际执行的是 MOVE（上轮完成）与两类合并——

`metrics_lsb20`→薄转发、`charge_ref`→薄转发；其余模块保留。

没有删除任何无法确认用途的文件。


## `(root)`

| 文件 | 行数 | 函数 | 类 | 公开面（前 4 个） |
|:---|---:|---:|---:|:---|
| `__init__.py` | 313 | 0 | 0 | — |
| `__main__.py` | 23 | 1 | 0 | main |
| `_arrays.py` | 9 | 1 | 0 | readonly |
| `calib.py` | 147 | 4 | 1 | _matrix, observability, group_aggregate, required_samples |
| `cli.py` | 384 | 11 | 3 | _unique_object, _finite_float, _reject_constant, _read_json_object |
| `codes.py` | 112 | 3 | 0 | to_unsigned, to_signed, midpoint_v |
| `defaults.py` | 822 | 7 | 3 | _positive, _seed, _segmented_mask, _all_signal |
| `hashing.py` | 123 | 4 | 0 | sha256_file, _strip_python, _strip_systemverilog, semantic_digest |
| `inventory_gate.py` | 756 | 12 | 3 | _load_json, _resolve_sources_dir, _check_reference_assets, _check_sources |
| `mechanisms.py` | 68 | 0 | 0 | — |
| `provenance.py` | 298 | 5 | 3 | _derived_grade, grade_of, _equal, annotate_config |
| `rtl_observation.py` | 474 | 12 | 0 | extract_bitfields, _required, _mapping, _digest |

## `behavioral`

| 文件 | 行数 | 函数 | 类 | 公开面（前 4 个） |
|:---|---:|---:|---:|:---|
| `__init__.py` | 5 | 0 | 0 | — |
| `adc2.py` | 101 | 0 | 1 | — |
| `charge_ref.py` | 22 | 0 | 0 | — |
| `charge_ref_task.py` | 171 | 3 | 0 | _network_inputs, ref_ra_charge, ref_ra_charge_nodal |
| `conversion.py` | 655 | 4 | 3 | _finite, _positive, _cascade_matrix, response_interval |
| `dynamics.py` | 90 | 2 | 0 | switching_activity, crosstalk_error |
| `eight_slice.py` | 257 | 8 | 3 | decode, words, native_step_v, coarse_decision |
| `impairments.py` | 215 | 2 | 1 | only, all_of |
| `input_network.py` | 361 | 3 | 3 | _finite_scalar, _linear_network, track_interval |
| `low_frequency_noise.py` | 141 | 0 | 1 | — |
| `noise.py` | 299 | 6 | 2 | thermal_sigma, _generator, white, flicker |
| `pretracking.py` | 262 | 2 | 4 | _finite_scalar, _physical_ids |
| `ra.py` | 147 | 1 | 1 | _finite_scalar |
| `ra_az.py` | 110 | 1 | 2 | apply_ordinary_az |
| `reference.py` | 259 | 5 | 1 | settle_error, precharge_fraction, dynamic_reference, required_tau |
| `reference_charge.py` | 285 | 4 | 1 | _real_array, _broadcast, sampled_bottom_voltages, physical_reference_program |
| `reference_mux.py` | 204 | 0 | 3 | — |
| `sadc.py` | 114 | 1 | 1 | build_first_stage_quantizer |
| `sampler.py` | 267 | 6 | 2 | finite_record, capture, dc_input, sine_input |
| `sampling_charge.py` | 81 | 1 | 0 | digital_connection_terms |
| `timing_jitter.py` | 199 | 5 | 1 | sine_slope, aperture_jitter, jitter_limited_snr, dac_pole_attenuation |
| `transients.py` | 282 | 2 | 5 | rail_recovery, ra_endpoint |

## `calibration`

| 文件 | 行数 | 函数 | 类 | 公开面（前 4 个） |
|:---|---:|---:|---:|:---|
| `__init__.py` | 1 | 0 | 0 | — |
| `mask_calibration.py` | 553 | 5 | 3 | _mask_matrix, fit_mask_weights, apply_mask_calibration, fit_code_correction |
| `reconstruction.py` | 59 | 2 | 1 | initialize_state, reconstruct |
| `weight_calibration.py` | 449 | 3 | 4 | _terms, fit_unit_weights, apply_frozen_calibration |

## `contracts`

| 文件 | 行数 | 函数 | 类 | 公开面（前 4 个） |
|:---|---:|---:|---:|:---|
| `__init__.py` | 47 | 0 | 0 | — |
| `events.py` | 33 | 0 | 1 | — |
| `fixed_point.py` | 286 | 1 | 3 | round_even_divide |
| `profiles.py` | 136 | 2 | 1 | load_profile, assert_caaz_free |
| `specs.py` | 239 | 1 | 8 | _require |
| `task_types.py` | 68 | 0 | 2 | — |

## `control`

| 文件 | 行数 | 函数 | 类 | 公开面（前 4 个） |
|:---|---:|---:|---:|:---|
| `__init__.py` | 9 | 0 | 0 | — |
| `codebook.py` | 73 | 2 | 0 | map_record, codebook |
| `dem.py` | 176 | 3 | 3 | _integer_array, _binary_mask, split_switch_command |
| `dem3d.py` | 501 | 7 | 1 | _mix64, fig11_masks, unary_rotation_masks, nominal_conservation |
| `dither.py` | 282 | 1 | 4 | complete_flash3_sar6_prefix_table |
| `dither_port.py` | 337 | 4 | 1 | code_shift_residual, range_ledger, attenuated_dither_bound, digital_descramble |
| `dither_word.py` | 90 | 0 | 2 | — |
| `fig11_dem.py` | 156 | 2 | 1 | _unsigned_field, map_fig11 |
| `fig21_bridge.py` | 83 | 1 | 1 | map_fig21_bridge |
| `follower.py` | 518 | 8 | 3 | _identity_event_codes, run_follower, run_direct_final, run_trial_sar |
| `module1.py` | 175 | 2 | 3 | advance_dem_rng, dem_snapshot |
| `module1_bridge.py` | 114 | 0 | 2 | — |
| `scheduler.py` | 156 | 1 | 2 | _advance_lfsr |
| `timing.py` | 105 | 1 | 1 | build_slice_plan |
| `track_aux.py` | 265 | 6 | 1 | build_track_plan, causality_audit, track_residual_charge, input_current_a |

## `metrics`

| 文件 | 行数 | 函数 | 类 | 公开面（前 4 个） |
|:---|---:|---:|---:|:---|
| `__init__.py` | 16 | 0 | 0 | — |
| `benchmarks.py` | 45 | 0 | 1 | — |
| `metrics.py` | 666 | 17 | 0 | _record, _window, power_spectral_density, integrate_noise_band |
| `metrics_lsb20.py` | 38 | 0 | 0 | — |

## `simulation`

| 文件 | 行数 | 函数 | 类 | 公开面（前 4 个） |
|:---|---:|---:|---:|:---|
| `__init__.py` | 1 | 0 | 0 | — |
| `config.py` | 502 | 2 | 3 | _finite_real, _integer |
| `pipeline.py` | 99 | 1 | 0 | run_pipeline |
| `pipeline_engine.py` | 816 | 6 | 0 | _reference_record, _reference_at, _binding_name, _same_book |
| `scheduler.py` | 54 | 1 | 2 | make_scheduler |
| `serialization.py` | 79 | 2 | 0 | result_document, write_results |
| `sim.py` | 192 | 0 | 1 | — |
| `slice_pool.py` | 279 | 1 | 1 | check_causality |

## `topology`

| 文件 | 行数 | 函数 | 类 | 公开面（前 4 个） |
|:---|---:|---:|---:|:---|
| `__init__.py` | 1 | 0 | 0 | — |
| `capacitors.py` | 196 | 4 | 2 | cap_pool, unpack, feedback_charge, voltage_equivalent |
| `dac_arch.py` | 460 | 9 | 5 | _finite_array, _sample_values, _active_reference, build_split_chip |
| `mapper.py` | 81 | 0 | 2 | — |
