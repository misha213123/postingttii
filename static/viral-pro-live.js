/* Viral Reels Pro v3: always-on local canvas mock-up of the current scene.
 * For frame-perfect export use the existing 3-second FFmpeg preview.
 * The canvas never uploads files, modifies sources or claims pixel-perfect effects.
 */
(() => {
  const cvs = document.getElementById('liveCanvas');
  if(!cvs)return;
  const ctx = cvs.getContext('2d',{willReadFrequently:true});
  const media = document.createElement('video');
  const backVideo = document.createElement('video');
  const bannerVideo = document.createElement('video');
  for(const video of [media,backVideo,bannerVideo]){
    video.muted=true;video.loop=true;video.playsInline=true;video.preload='metadata';
  }
  const image = new Image(), bannerImage=new Image();
  const scratch=document.createElement('canvas');
  const sx=scratch.getContext('2d',{willReadFrequently:true});
  let mediaId='',backgroundKey='',bannerKey='',backgroundIsVideo=false,bannerIsVideo=false;
  let bannerShape={x:0,y:0,w:0,h:0}, drag=null, eyedropper=false,lastFrame=0;
  let play=true;
  const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
  const videoExt=name=>/\.(mp4|mov|webm|m4v)$/i.test(name||'');
  const ready=v=>v && v.readyState>=2 && v.videoWidth>0;
  const scene=()=>Math.max(0,Math.min(Number(document.getElementById('liveScene')?.value||0),
                      (project?.config?.clips?.length||1)-1));
  const currentClip=()=>project?.config?.clips?.[scene()];
  const source=element=>element instanceof HTMLVideoElement ? ready(element) : element.complete && element.naturalWidth;
  function cover(element,x,y,w,h){
    if(!source(element))return false;
    const iw=element.videoWidth||element.naturalWidth,ih=element.videoHeight||element.naturalHeight;
    const scale=Math.max(w/iw,h/ih),sw=w/scale,sh=h/scale;
    ctx.drawImage(element,(iw-sw)/2,(ih-sh)/2,sw,sh,x,y,w,h);
    return true;
  }
  function setVideo(video,url,onReady){
    video.pause();video.removeAttribute('src');video.load();
    video.src=url;
    video.onloadedmetadata=()=>{onReady?.();if(play)video.play().catch(()=>{});};
    if(play)video.play().catch(()=>{});
  }
  function sync(){
    if(!project)return;
    const cfg=project.config, clip=currentClip();
    const mid=clip?.media_id||'';
    if(mid!==mediaId){
      mediaId=mid;
      if(mid)setVideo(media,API+'/media/'+encodeURIComponent(mid),()=>{
        media.currentTime=Number(currentClip()?.start||0);
      });
      else {media.pause();media.removeAttribute('src');media.load()}
    }
    const bgKey=cfg.background_name?cfg.background_source+':'+cfg.background_name:'';
    if(bgKey!==backgroundKey){
      backgroundKey=bgKey;const n=cfg.background_name||'';
      backgroundIsVideo=videoExt(n);
      if(n){
        const kind=cfg.background_source==='local'?'background':'studio-background';
        const url=API+'/preview-asset/'+kind+'/'+encodeURIComponent(n);
        if(backgroundIsVideo)setVideo(backVideo,url);
        else image.src=url;
      } else{image.removeAttribute('src');backVideo.pause();}
    }
    const bn=cfg.banner_enabled?(cfg.banner_file||'studio-default'):'';
    if(bn!==bannerKey){
      bannerKey=bn;bannerIsVideo=videoExt(cfg.banner_file);
      if(bn){
        const url=cfg.banner_file?API+'/preview-asset/banner/'+encodeURIComponent(cfg.banner_file):
                  API+'/preview-studio-banner';
        if(bannerIsVideo)setVideo(bannerVideo,url);
        else bannerImage.src=url;
      }else{bannerImage.removeAttribute('src');bannerVideo.pause();}
    }
  }
  const fill=(element,x,y,w,h)=>{
    if(!source(element))return false;
    const iw=element.videoWidth||element.naturalWidth,ih=element.videoHeight||element.naturalHeight;
    const ratio=iw/ih;
    const dh=w/ratio;
    ctx.drawImage(element,x,y,w,dh);
    return dh;
  };
  function draw(now=0){
    requestAnimationFrame(draw);
    if(now-lastFrame<55)return;lastFrame=now;
    if(!project)return;
    sync();
    const cfg=project.config,w=540,h=960;
    ctx.clearRect(0,0,w,h);
    const gradient=ctx.createLinearGradient(0,0,w,h);
    gradient.addColorStop(0,'#294d6e');gradient.addColorStop(1,'#101727');
    ctx.fillStyle=gradient;ctx.fillRect(0,0,w,h);
    ctx.filter='contrast('+Number(cfg.contrast||1)+') saturate('+Number(cfg.saturation||1)+')';
    cover(backgroundIsVideo?backVideo:image,0,0,w,h);ctx.filter='none';
    const clip=currentClip(), start=Number(clip?.start||0),end=Number(clip?.end??media.duration);
    if(ready(media)&&play&&Number.isFinite(end)&&end>start&&media.currentTime>=end-.04)media.currentTime=start;
    const x=Number(cfg.circle_x||0)/2,y=Number(cfg.circle_y||0)/2,r=Number(cfg.circle_size||800)/2;
    ctx.save();ctx.beginPath();ctx.arc(x+r/2,y+r/2,r/2,0,Math.PI*2);ctx.clip();
    if(!cover(media,x,y,r,r)){
      ctx.fillStyle='#213e58';ctx.fillRect(x,y,r,r);
      ctx.fillStyle='#f4f8ff';ctx.font='bold 15px sans-serif';ctx.textAlign='center';
      ctx.fillText(clip?'▶ Видео загружается':'Загрузи кружок',x+r/2,y+r/2);
    }
    ctx.restore();
    bannerShape={x:0,y:0,w:0,h:0};
    if(cfg.banner_enabled){
      const asset=bannerIsVideo?bannerVideo:bannerImage;
      if(source(asset)){
        const bw=Number(cfg.banner_width||600)/2,bx=Number(cfg.banner_x||0)/2,
              by=Number(cfg.banner_y||0)/2;
        const iw=asset.videoWidth||asset.naturalWidth,ih=asset.videoHeight||asset.naturalHeight;
        const bh=Math.max(1,Math.round(bw*ih/iw));
        bannerShape={x:bx,y:by,w:bw,h:bh};
        const t=ready(media)?media.currentTime-start:0;
        if(t>=Number(cfg.banner_start||0)&&t<=Number(cfg.banner_end??3600)){
          scratch.width=Math.max(1,Math.round(bw));scratch.height=Math.max(1,Math.round(bh));
          sx.clearRect(0,0,scratch.width,scratch.height);
          sx.drawImage(asset,0,0,scratch.width,scratch.height);
          if(cfg.banner_chroma_enabled){
            const rgb=cfg.banner_chroma_color||'#00FF00';
            const selected=[1,3,5].map(i=>parseInt(rgb.slice(i,i+2),16));
            const img=sx.getImageData(0,0,scratch.width,scratch.height),d=img.data;
            const similarity=Number(cfg.banner_chroma_similarity||.22)*441.7;
            const blend=Math.max(1,Number(cfg.banner_chroma_blend||.07)*441.7);
            for(let i=0;i<d.length;i+=4){
              const dist=Math.hypot(d[i]-selected[0],d[i+1]-selected[1],d[i+2]-selected[2]);
              const alpha=clamp((dist-similarity)/blend,0,1);
              d[i+3]=Math.round(d[i+3]*alpha);
            }
            sx.putImageData(img,0,0);
          }
          ctx.drawImage(scratch,bx,by,bw,bh);
        }
      }
    }
    const duration=Number.isFinite(end)&&end>start?end-start:media.duration;
    const elapsed=ready(media)?Math.max(0,media.currentTime-start):0;
    const font=cfg.caption_font||'Arial',size=Number(cfg.caption_font_size||48)/2;
    let captionOffset=0;
    for(let i=0;i<scene();i++){
      const c=project.config.clips[i];captionOffset+=Math.max(0,Number(c.end||0)-Number(c.start||0));
    }
    const active=(cfg.captions||[]).find(t=>elapsed+captionOffset>=t.start && elapsed+captionOffset<t.end);
    if(active){
      ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.font=(cfg.caption_template==='minimal'?'normal ':'bold ')+size+'px "'+font+'",sans-serif';
      ctx.lineJoin='round';
      if(cfg.caption_template==='box'){
        const measure=ctx.measureText(active.text).width;
        ctx.fillStyle='#111827c9';ctx.fillRect(270-measure/2-10,Number(cfg.caption_y||820)/2-size/2-7,measure+20,size+14);
      }
      ctx.strokeStyle='#050505';ctx.lineWidth=cfg.caption_template==='minimal'?1:4;
      ctx.strokeText(active.text,270,Number(cfg.caption_y||820)/2,510);
      ctx.fillStyle=cfg.caption_template==='highlight'?'#FFE237':cfg.caption_color||'#FFFFFF';
      ctx.fillText(active.text,270,Number(cfg.caption_y||820)/2,510);
    }
    ctx.fillStyle='#ffffffbd';ctx.font='11px Arial';ctx.textAlign='left';ctx.textBaseline='alphabetic';
    ctx.fillText('Сцена '+(scene()+1)+' · '+elapsed.toFixed(1)+' c'+(Number.isFinite(duration)?' / '+duration.toFixed(1)+' c':''),10,945);
    if(eyedropper){ctx.fillStyle='#ffffffdb';ctx.fillRect(6,8,235,29);ctx.fillStyle='#141d31';ctx.font='bold 12px Arial';ctx.fillText('Нажми на цвет фона баннера',12,27);}
  }
  function point(ev){
    const rect=cvs.getBoundingClientRect();
    return {x:(ev.clientX-rect.left)*540/rect.width,y:(ev.clientY-rect.top)*960/rect.height};
  }
  cvs.addEventListener('pointerdown',ev=>{
    if(!project)return;const p=point(ev),cfg=project.config;
    if(eyedropper){
      if(bannerShape.w && p.x>=bannerShape.x&&p.x<=bannerShape.x+bannerShape.w &&
        p.y>=bannerShape.y&&p.y<=bannerShape.y+bannerShape.h&&scratch.width){
        const data=sx.getImageData(Math.max(0,Math.min(scratch.width-1,Math.floor(p.x-bannerShape.x))),
                                 Math.max(0,Math.min(scratch.height-1,Math.floor(p.y-bannerShape.y))),1,1).data;
        const hex='#'+[data[0],data[1],data[2]].map(v=>v.toString(16).padStart(2,'0')).join('').toUpperCase();
        cfg.banner_chroma_color=hex;
        document.getElementById('bannerChromaColor').value=hex;
        cfg.banner_chroma_enabled=true;document.getElementById('bannerChroma').checked=true;
        alertStatus('Пипетка выбрала '+hex+' — зелёный/синий фон будет удаляться при экспорте.');
      }else alertStatus('Нажми на участок баннера внутри миниатюры',true);
      eyedropper=false;document.getElementById('eyeDropper').classList.remove('active');
      return;
    }
    const bx=bannerShape.x,by=bannerShape.y,bw=bannerShape.w,bh=bannerShape.h;
    const circleX=Number(cfg.circle_x||0)/2,circleY=Number(cfg.circle_y||0)/2,rad=Number(cfg.circle_size||900)/4;
    const withinBanner=cfg.banner_enabled&&p.x>=bx&&p.x<=bx+bw&&p.y>=by&&p.y<=by+bh;
    const withinCircle=Math.hypot(p.x-(circleX+rad),p.y-(circleY+rad))<=rad;
    if(!withinCircle&&!withinBanner)return;
    drag={kind:withinBanner?'banner':'circle',start:p,x:cfg[withinBanner?'banner_x':'circle_x'],y:cfg[withinBanner?'banner_y':'circle_y']};
    cvs.setPointerCapture(ev.pointerId);cvs.style.cursor='grabbing';ev.preventDefault();
  });
  cvs.addEventListener('pointermove',ev=>{
    if(!drag||!project)return;
    const p=point(ev),cfg=project.config;
    const prefix=drag.kind==='banner'?'banner':'circle';
    const w=prefix==='circle'?Number(cfg.circle_size||900):Number(cfg.banner_width||600);
    cfg[prefix+'_x']=Math.round(clamp(drag.x+(p.x-drag.start.x)*2,0,1080-w));
    cfg[prefix+'_y']=Math.round(clamp(drag.y+(p.y-drag.start.y)*2,0,1920-100));
    document.getElementById(prefix==='circle'?'circleX':'bannerX').value=cfg[prefix+'_x'];
    document.getElementById(prefix==='circle'?'circleY':'bannerY').value=cfg[prefix+'_y'];
    drawGuides();
  });
  const finish=async()=>{
    if(!drag)return;drag=null;cvs.style.cursor='grab';
    try{await saveProject()}catch(e){alertStatus('Позиция: '+e.message,true)}
  };
  cvs.addEventListener('pointerup',finish);cvs.addEventListener('pointercancel',finish);
  document.getElementById('eyeDropper')?.addEventListener('click',()=>{
    eyedropper=!eyedropper;
    document.getElementById('eyeDropper').classList.toggle('active',eyedropper);
    alertStatus(eyedropper?'Нажми на цвет хромакея внутри баннера на миниатюре':'Пипетка выключена');
  });
  document.getElementById('livePause')?.addEventListener('click',()=>{
    play=!play;
    for(const v of [media,backVideo,bannerVideo]){if(play)v.play().catch(()=>{});else v.pause();}
    document.getElementById('livePause').textContent=play?'⏸ Пауза':'▶ Продолжить';
  });
  document.getElementById('liveScene')?.addEventListener('change',()=>{
    mediaId='';sync();
  });
  window.ViralLive={sync,refreshScenes(){
    const el=document.getElementById('liveScene');
    const current=Number(el.value||0);
    el.innerHTML=(project?.config?.clips||[]).map((_,i)=>
      '<option value="'+i+'">Сцена '+(i+1)+'</option>').join('');
    el.value=String(Math.min(current,Math.max(0,(project?.config?.clips?.length||1)-1)));
    mediaId='';sync();
  }};
  cvs.style.cursor='grab';
  requestAnimationFrame(draw);
})();