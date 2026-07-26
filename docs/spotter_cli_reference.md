# Spotter CLI Reference (bench ebox over Mac USB)

Command list captured from the Spotter terminal `help` output (Nick,
2026-07-26). Shared reference for Sprint09 (UART throughput) and Sprint10
(command daemon) bench work. Connect a serial terminal to the Spotter's
USB port; log the terminal session to a file when capturing test output.

## Most-used for our sprints

### SD filesystem (Sprint09 Phase A verification)
```
ls                      # list files
cd <directory>
pwd
cat <filename>          # print entire file (CAREFUL WITH LARGE FILES)
head <filename>         # first 1 kB
tail <filename>         # last 1 kB
sd usb                  # expose SD over USB as READ-ONLY mass storage
sd usbrw                # same, READ-WRITE (avoid unless needed)
sd err                  # print SD write/read error counts
```
Pulling `uart_test.log` (~60 kB for a 200-line Phase A run): either
`cat uart_test.log` with terminal session logging enabled, or `sd usb` to
mount the card on the Mac and copy the file. `sd usb` requires care —
confirm normal Spotter logging resumes after unmount.

### Cellular / transmission diagnostics (Sprint09 Phase B)
```
post                    # POST status incl. cellularSignalErrorState /
                        # cellularErrorState — both "OK" = data reaching
                        # Sofar backend (per forum t/575)
sdmq size               # SD message queue depth
sdmq get                # dequeue + print one message
note ...                # notecard modem controls (enable/disable/sync/tx)
```

### BM bus (Sprint10 Phase B command injection + node inspection)
```
bm topo                                   # bus topology / node ids
bm info <node_id>
bm pub <topic> <data> <type> <version>    # publish onto the BM bus —
                                          # candidate path for injecting
                                          # test commands to the camera
                                          # node from the bench
bm cfg get|set|commit|clear|status|del ...  # node config partitions (u/s/h)
bridge cfg get|set|commit|status|del ...    # same, via bridge
bridge baudrate <57600|115200|1M>         # bridge baud — relevance to the
                                          # mote<->Pi payload UART UNVERIFIED;
                                          # investigate before firmware-tier
                                          # baud work
smsync                                    # send full bm config to wavefleet
```

### General
```
help            # list all commands
info            # device information
uptime
sensors         # most recent sensor data
log list|dest|level|flush
cfg / hwcfg     # Spotter configuration (list/get/set/save)
rtc set YYYY MM DD hh mm ss
gps / gpspwr
error debug     # print all error values
memfault print  # metrics
```

## Danger zone — do not use casually
```
sd format             # DESTROYS SD contents
cfg defaults          # resets all Spotter config
reset / debug reset   # reboots the Spotter (kills bus power to nodes)
debug crash|hardfault|null|hang   # deliberate fault injection
bootloader / debug bootloader / update <file>  # firmware update paths
bridge dfu / bridge bootloader    # mote firmware update paths
note restore          # factory-resets notecard
```
Per CLAUDE.md field-ops rules: nothing in this list gets run against a
deployed unit without an explicit plan and rollback.

## Full raw help output

<details>
<summary>Verbatim capture (2026-07-26)</summary>

```
help, post, hwcfg, cfg, sst, cal, log, sd (format|benchmark|err|usb|usbrw|
update|count), ls, cat, head, tail, pwd, cd, sensors, error (set|reset|
debug), info, debug (reset|crash|hardfault|null|mem|tasks|hang|bootloader),
uptime, reset, bootloader, sdmq (add|get|size), update, rtc, gpio (list|
set|clr|get), orch (cfg|err), sensor (enable|disable SST|HTU|BARO|PWR),
memfault (test|trig|print), gps, gpspwr, bridge (test|rtc|dfu|baudrate|
bootloader|hw_version|cfg get/set/commit/status/del), smsync,
bm (topo|cfg get/set/commit/clear/status/del|pub|info|resources), note
(enable|disable|tx|txs|sync|txd|rx|rxd|updateinfo|restore|extsiminfo|
fwclr|fwupdate)
```

</details>
