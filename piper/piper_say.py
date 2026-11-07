#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""piper KSS(한국어) onnx 추론 — pygoruut G2P + 코드포인트 음소맵. CLI: piper_say.py "문장" [out.wav]"""
import json, sys, wave, numpy as np, onnxruntime as ort
from pygoruut.pygoruut import Pygoruut
BASE="/home/ryuch/smart-cabin/piper"
CFG=BASE+"/piper-kss-korean.onnx.json"; MODEL=BASE+"/piper-kss-korean.onnx"
d=json.load(open(CFG)); pm=d["phoneme_id_map"]; sr=d["audio"]["sample_rate"]
inf=d["inference"]; ns,ls,nw=inf["noise_scale"],inf["length_scale"],inf["noise_w"]
text=sys.argv[1] if len(sys.argv)>1 else "안녕하세요"
out=sys.argv[2] if len(sys.argv)>2 else "/tmp/piper.wav"
g=Pygoruut()
res=g.phonemize(language="Korean", sentence=text)
phon=" ".join(w.Phonetic for w in res.Words)
drop=sorted({c for c in phon if c not in pm and c!=" "})
ids=list(pm["^"])+list(pm["_"])
for ch in phon:
    if ch in pm:
        ids+=pm[ch]; ids+=pm["_"]
ids+=pm["$"]
sess=ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
names=[i.name for i in sess.get_inputs()]
feed={"input":np.array([ids],dtype=np.int64),
      "input_lengths":np.array([len(ids)],dtype=np.int64),
      "scales":np.array([ns,ls,nw],dtype=np.float32)}
if "sid" in names: feed["sid"]=np.array([0],dtype=np.int64)
feed={k:v for k,v in feed.items() if k in names}
audio=sess.run(None,feed)[0].squeeze().astype(np.float32)
audio=audio/(np.max(np.abs(audio))+1e-9)
pcm=(audio*32767*0.9).astype("<i2")
w=wave.open(out,"wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes()); w.close()
print("inputs:",names,"| ids:",len(ids),"| 음소:",phon,"| 맵없음(무시):",drop,"| dur %.2fs"%(len(audio)/sr),"->",out)
