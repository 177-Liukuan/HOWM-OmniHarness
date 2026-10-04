"""Deterministic original-media acquisition; no model supplied commands/paths."""
import hashlib, io, json, re, subprocess, time
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps
from vendor.prepare_evidence import referenced_file, sha256, font, save_image
from contracts import require,request_key


def load_question(data,qid):
    require(isinstance(qid,str) and qid.isascii() and qid.isdecimal() and qid==str(int(qid)).zfill(3),'use original canonical string question_id')
    directory=(data/qid).resolve(); require(directory.is_relative_to(data.resolve()),'question escapes root')
    q=json.loads((directory/'question.json').read_text())
    require(q['question_id']==qid and isinstance(q['instruction'],str),'wrong question schema')
    require([o['option_id'] for o in q['options']]==[str(i) for i in range(1,31)],'expected original ordered 30 options')
    for rel in [q['init'],q['final'],*[o['video'] for o in q['options']]]:
        require(isinstance(rel,str) and not Path(rel).is_absolute(),'absolute media path rejected')
        f=referenced_file(directory,rel); require(f.is_file() and f.stat().st_size>0,'missing/empty media')
    return q,directory


def original_frame(path,t,timeout):
    cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','info','-threads','1','-filter_threads','1','-i',str(path),'-vf',f"setpts=PTS-STARTPTS,select='gte(t,{t:.6f})',showinfo",'-frames:v','1','-threads','1','-f','image2pipe','-vcodec','png','pipe:1']
    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    stderr=p.stderr.decode(errors='replace')
    require(p.returncode==0 and p.stdout,'frame extraction failed: '+stderr[-500:])
    times=re.findall(r'\bn:\s*\d+\s+pts:.*?\bpts_time:([\d.eE+-]+)',stderr)
    require(bool(times),'missing actual frame timestamp')
    return Image.open(io.BytesIO(p.stdout)).convert('RGB'),float(times[0])


def acquire(q,directory,r,output,remaining):
    started=time.monotonic()
    layout={'version':2,'columns':2,'frame_width':512,'frame_height':288,'label_height':32}
    identity=hashlib.sha256((request_key(r)+json.dumps(layout,sort_keys=True)).encode()).hexdigest()[:16]
    prefix=(f"option_{r['option_id']:02d}_sample" if r['action']=='sample_video' else f"option_{r['option_id']:02d}_crop" if r['source']=='video' else f"{r['source']}_crop")
    name=f'{prefix}_{identity}.jpg'; path=output/name
    source=referenced_file(directory,q[r['source']] if r['action']=='crop_evidence' and r['source']!='video' else q['options'][r['option_id']-1]['video'])
    source_hash=sha256(source)
    metadata={'request':r,'source_sha256':source_hash,'source':str(source),'question_id':q['question_id'],'id':name,'layout':layout,'cache_hit':False}
    saved=path.with_suffix('.json')
    if path.exists() and saved.exists():
        cached=json.loads(saved.read_text())
        if cached.get('source_sha256')==source_hash and cached.get('layout')==layout and cached['image']['sha256']==sha256(path) and request_key(cached['request'])==request_key(r):
            return name,{**cached,'cache_hit':True,'execution_seconds':time.monotonic()-started,'decoded_frames':0}
    if r['action']=='sample_video':
        a,b=r['time_range_seconds']; times=[a+(b-a)*i/(r['frames']-1) for i in range(r['frames'])]
        sheet=Image.new('RGB',(1024,320*((len(times)+1)//2)),(245,245,245)); draw=ImageDraw.Draw(sheet)
        actual=[]
        for i,t in enumerate(times):
            frame,pts=original_frame(source,t,min(120,remaining())); actual.append(pts)
            x=(i%2)*512; y=(i//2)*320
            draw.text((x+4,y+3),f"Option {r['option_id']} | t={pts:.3f}s",font=font(22),fill='black')
            sheet.paste(ImageOps.pad(frame,(512,288),method=Image.Resampling.LANCZOS,color='black'),(x,y+32))
        require(actual==sorted(actual),'frames must be in timestamp order')
        metadata.update(requested_times=times,actual_times=actual,unique_frames=len(set(actual)),decoded_frames=len(actual))
        require(len(set(actual))>=2,'requested interval produced fewer than two distinct frames')
    else:
        if r['source']=='video':
            frame,pts=original_frame(source,r['time_seconds'],min(120,remaining())); metadata['actual_time']=pts; metadata['decoded_frames']=1
        else:
            frame=ImageOps.exif_transpose(Image.open(source)).convert('RGB')
        w,h=frame.size; b=r['bbox']; box=(int(b[0]*w),int(b[1]*h),int(b[2]*w),int(b[3]*h))
        require(box[2]>box[0] and box[3]>box[1],'empty pixel crop')
        sheet=frame.crop(box); metadata.update(original_size=[w,h],pixel_bbox=box)
    metadata['image']=save_image(path,sheet)
    metadata['execution_seconds']=time.monotonic()-started
    path.with_suffix('.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    return name,metadata
