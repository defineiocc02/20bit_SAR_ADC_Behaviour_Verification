# Generate one reference CDL for both decks from the same frozen routed ODB.
read_liberty $::env(LVS_LIBERTY)
read_db /work/input/$::env(LVS_TOP).odb
read_sdc /work/input/$::env(LVS_TOP).sdc
write_cdl -masters /work/out/compatible.cdl /work/out/top.cdl
