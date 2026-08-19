#!/bin/sh
# /usr/lib/systemd/system-sleep/barkeep
#
# Tear the Touch Bar display session down BEFORE the kernel suspends, and put
# it back after resume.
#
# Why this exists rather than relying on the driver's own .suspend/.resume:
# barkeep-dfr holds the display session open by continuously streaming frames,
# and a USB driver still submitting URBs while the USB core is trying to
# quiesce the device wedges the whole suspend - the machine stops at
# "PM: suspend entry (deep)" and never comes back, taking the display and
# touchpad with it. The in-kernel PM callbacks are the correct fix and are
# still there, but this runs earlier, in process context, where systemd waits
# for us - so by the time the kernel begins suspending there is no driver bound
# to the device at all and nothing to get wrong.
#
# systemd-sleep runs this with "pre" before sleeping and "post" after waking,
# and blocks until it exits. Every step is time-bounded so a hang here cannot
# stall the suspend indefinitely.

case "$1" in
    pre)
        logger -t barkeep-sleep "stopping the display session before $2"
        timeout 60 systemctl stop barkeep-bar.service barkeep-display.service
        # Belt and braces: if the unit stop did not unload it, force it. No
        # module bound means no URBs in flight when the USB core suspends.
        timeout 15 rmmod barkeep_dfr 2>/dev/null
        logger -t barkeep-sleep "display session down"
        ;;
    post)
        logger -t barkeep-sleep "restoring the display session after $2"
        timeout 120 systemctl start barkeep-display.service
        timeout 60  systemctl start barkeep-bar.service
        logger -t barkeep-sleep "display session restored"
        ;;
esac
exit 0
