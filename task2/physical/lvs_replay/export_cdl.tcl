# Reuse the frozen routed database and the same CDL export command as ORFS.
read_liberty $::env(LVS_LIBERTY)
read_db /work/input/dither8_top.odb
read_sdc /work/input/dither8_top.sdc
write_cdl -masters /work/out/compatible.cdl /work/out/dither8_top.cdl
