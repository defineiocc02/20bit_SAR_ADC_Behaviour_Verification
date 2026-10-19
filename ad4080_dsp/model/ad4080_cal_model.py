import random, math
IN_W=20; FS=(1<<IN_W)-1
X1=104858; X2=943718; DX=X2-X1
K=0.015594/512
SHIFT_K=20; INV_DX_K=41041
SHIFT_O=30; INV_DX=1280
K_GAIN=511; SHIFT_G=24

def clip(v, lo, hi): return lo if v<lo else hi if v>hi else v

def sys_corr(x, off, gain):
    gd = gain-512
    delta = (x*gd*K_GAIN + (1<<(SHIFT_G-1))) >> SHIFT_G
    y = x + delta + off
    return clip(y,0,FS), (y<0), (y>FS), delta

def cal_two(y1,y2):
    dy = y2-y1
    num_g = dy-DX
    gd = (num_g*INV_DX_K + (1<<(SHIFT_K-1))) >> SHIFT_K
    gain = clip(512+gd,0,1023)
    offnum = y1*DX - dy*X1
    oc = (offnum*INV_DX + (1<<(SHIFT_O-1))) >> SHIFT_O
    off = clip(oc,-2048,2047)
    return gain, off

def ideal_y(x,m,c): return round(m*x+c)

def test_case(name,x1,x2,m,c):
    y1=clip(ideal_y(x1,m,c),0,FS); y2=clip(ideal_y(x2,m,c),0,FS)
    g,o=cal_two(y1,y2)
    # apply correction to a few x
    errs=[]
    for x in [0,1,1000,FS//4,FS//2,3*FS//4,FS-1]:
        yt=clip(ideal_y(x,m,c),0,FS)
        yc,lo,hi,_=sys_corr(x,o,g)
        errs.append(yc-yt)
    print(f'{name:24s} y1={y1:7d} y2={y2:7d} -> GAIN={g:4d} OFF={o:6d} maxerr={max(abs(e) for e in errs):4d} LSB')

# nominal
test_case('ideal',X1,X2,1.0,0.0)
test_case('gain +1%',X1,X2,1.01,0.0)
test_case('gain -1%',X1,X2,0.99,0.0)
test_case('offset +100',X1,X2,1.0,100.0)
test_case('offset -100',X1,X2,1.0,-100.0)
test_case('gain +0.5% off +50',X1,X2,1.005,50.0)
test_case('gain -0.5% off -50',X1,X2,0.995,-50.0)

# random two-point consistency
random.seed(1)
worst=0
for _ in range(2000):
    m=1+random.uniform(-0.01,0.01); c=random.uniform(-200,200)
    y1=clip(round(m*X1+c),0,FS); y2=clip(round(m*X2+c),0,FS)
    g,o=cal_two(y1,y2)
    for x in [random.randrange(FS) for _ in range(5)]:
        yt=clip(round(m*x+c),0,FS)
        yc,_,_,_=sys_corr(x,o,g)
        worst=max(worst,abs(yc-yt))
print('random max error (LSB):',worst)
