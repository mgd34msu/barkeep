# Shared helper, sourced by the bring-up/teardown scripts.
#
# Locate the Apple iBridge in sysfs by USB id rather than assuming a port. It
# happens to be 1-3 on a MacBookPro13,2, but that is a property of where the
# controller sits, not of the device, so hardcoding it breaks on any other
# machine - and on this one if the topology ever changes.
#
# The path is stable across the authorized 0/1 re-enumeration that dfr-up.sh
# does, because it names the port, not the enumeration.

ibridge_path() {
    local d
    for d in /sys/bus/usb/devices/*; do
        [ -f "$d/idProduct" ] || continue
        [ "$(cat "$d/idVendor"  2>/dev/null)" = "05ac" ] || continue
        [ "$(cat "$d/idProduct" 2>/dev/null)" = "8600" ] || continue
        echo "$d"
        return 0
    done
    return 1
}

# Same thing, but fail loudly - for scripts that cannot continue without it.
ibridge_path_or_die() {
    local d
    d=$(ibridge_path) || {
        echo "no Apple iBridge (05ac:8600) in /sys/bus/usb/devices - is this a T1 Mac?" >&2
        return 1
    }
    echo "$d"
}
