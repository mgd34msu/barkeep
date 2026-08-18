import glob, fcntl, os, sys
def rid_of(sd):
    try: d=open(os.path.join(sd,'report_descriptor'),'rb').read()
    except Exception: return None
    i=0;up=None;r=None
    while i<len(d):
        b=d[i];sz=b&3;sz=4 if sz==3 else sz
        t=(b>>2)&3;g=(b>>4)&0xF
        v=int.from_bytes(d[i+1:i+1+sz],'little') if sz else 0
        if t==1 and g==0: up=v
        if t==1 and g==8: r=v
        if t==2 and g==0 and up==0xff12 and v==0x21: return r
        i+=1+sz
    return None
def ioc(l): return (3<<30)|(l<<16)|(ord('H')<<8)|0x06
ok=0
for hd in sorted(glob.glob('/sys/bus/hid/devices/*')):
    r=rid_of(hd); raws=glob.glob(os.path.join(hd,'hidraw','hidraw*'))
    if r is None or not raws: continue
    n='/dev/'+os.path.basename(raws[0]); buf=bytearray(11); buf[0]=r; buf[1]=1; buf[2]=1
    try:
        fd=os.open(n,os.O_RDWR); fcntl.ioctl(fd,ioc(len(buf)),bytes(buf)); os.close(fd); ok+=1
    except Exception: pass
sys.exit(0 if ok else 1)
