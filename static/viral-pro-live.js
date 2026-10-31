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
  let bannerShape={x:0,y:0,w:0,h:0}, captionShape={x:0,y:0,w:0,h:0};
  let circleShape={x:0,y:0,w:0,h:0}, drag=null, eyedropper=false,lastFrame=0;
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
  function coverZoom(element, zoom){
    if(!source(element))return false;
    const iw=element.videoWidth||element.naturalWidth, ih=element.videoHeight||element.naturalHeight;
    const factor=Math.max(540/iw,960/ih)*zoom;
    const sw=540/factor,sh=960/factor;
    ctx.drawImage(element,(iw-sw)/2,(ih-sh)/2,sw,sh,0,0,540,960);
    return true;
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
    coverZoom(backgroundIsVideo?backVideo:image,Number(cfg.background_zoom||1));ctx.filter='none';
    const clip=currentClip(), start=Number(clip?.start||0),end=Number(clip?.end??media.duration);
    if(ready(media)&&play&&Number.isFinite(end)&&end>start&&media.currentTime>=end-.04)media.currentTime=start;
    const x=Number(cfg.circle_x??0)/2,y=Number(cfg.circle_y??0)/2,
          r=Number(cfg.circle_size||800)/2;
    circleShape={x,y,w:r,h:r};
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
      const c=project.config.clips[i];
      captionOffset+=Math.max(0,Number(c.end||0)-Number(c.start||0));
    }
    const active=(cfg.captions||[]).find(t=>elapsed+captionOffset>=t.start && elapsed+captionOffset<t.end);
    const placeholder=!(cfg.captions||[]).length;
    const words=active?.text || (placeholder?'СУБТИТРЫ':'Субтитры · положение');
    const capX=Number(cfg.caption_x??540)/2, capY=Number(cfg.caption_y??820)/2;
    ctx.textAlign='center';ctx.textBaseline='middle';
    ctx.font=(cfg.caption_template==='minimal'?'normal ':'bold ')+size+'px "'+font+'",sans-serif';
    const measure=Math.min(510,ctx.measureText(words).width);
    captionShape={
      x:clamp(capX-measure/2-10,0,539), y:clamp(capY-size*.75-7,0,959),
      w:Math.min(540,measure+20), h:Math.max(26,size*1.5+14)
    };
    if(active || placeholder){
      ctx.save();
      ctx.globalAlpha=active?1:.55;
      ctx.lineJoin='round';
      if(cfg.caption_template==='box'){
        ctx.fillStyle='#111827c9';
        ctx.fillRect(captionShape.x,captionShape.y,captionShape.w,captionShape.h);
      }
      ctx.strokeStyle='#050505';ctx.lineWidth=cfg.caption_template==='minimal'?1:4;
      ctx.strokeText(words,capX,capY,510);
      ctx.fillStyle=cfg.caption_template==='highlight'?'#FFE237':cfg.caption_color||'#FFFFFF';
      ctx.fillText(words,capX,capY,510);
      ctx.restore();
    }
    // Editing handles are preview-only overlays and never enter the exported MP4.
    function handle(x,y){
      ctx.fillStyle='#3e92ff';ctx.fillRect(x-7,y-7,14,14);
      ctx.strokeStyle='#fff';ctx.lineWidth=1.7;ctx.strokeRect(x-7,y-7,14,14);
    }
    ctx.save();
    ctx.lineWidth=1.5;ctx.strokeStyle='#86ccff';
    ctx.setLineDash([6,4]);
    ctx.strokeRect(circleShape.x,circleShape.y,circleShape.w,circleShape.h);
    ctx.setLineDash([]);handle(x+r,y+r);
    if(cfg.banner_enabled && bannerShape.w){
      ctx.strokeStyle='#ffb66c';ctx.setLineDash([5,4]);
      ctx.strokeRect(bannerShape.x,bannerShape.y,bannerShape.w,bannerShape.h);
      ctx.setLineDash([]);handle(bannerShape.x+bannerShape.w,bannerShape.y+bannerShape.h);
    }
    ctx.strokeStyle='#edc7ff';ctx.setLineDash([4,3]);
    ctx.strokeRect(captionShape.x,captionShape.y,captionShape.w,captionShape.h);
    ctx.setLineDash([]);handle(captionShape.x+captionShape.w,captionShape.y+captionShape.h);
    // Background is full-screen; its handle changes zoom, not output dimensions.
    ctx.fillStyle='#1f538bd9';ctx.fillRect(8,885,83,39);
    ctx.fillStyle='#fff';ctx.font='bold 11px Arial';ctx.textAlign='left';
    ctx.fillText('↗ ФОН '+Number(cfg.background_zoom||1).toFixed(2)+'×',14,906);
    ctx.restore();
    ctx.fillStyle='#ffffffbd';ctx.font='11px Arial';ctx.textAlign='left';ctx.textBaseline='alphabetic';
    ctx.fillText('Сцена '+(scene()+1)+' · '+elapsed.toFixed(1)+' c'+(Number.isFinite(duration)?' / '+duration.toFixed(1)+' c':''),10,945);
    if(eyedropper){ctx.fillStyle='#ffffffdb';ctx.fillRect(6,8,235,29);ctx.fillStyle='#141d31';ctx.font='bold 12px Arial';ctx.fillText('Нажми на цвет фона баннера',12,27);}
  }
  function point(ev){
    const rect=cvs.getBoundingClientRect();
    return {x:(ev.clientX-rect.left)*540/rect.width,y:(ev.clientY-rect.top)*960/rect.height};
  }
  const inBox=(p,shape)=>shape.w>0&&p.x>=shape.x&&p.x<=shape.x+shape.w&&
     p.y>=shape.y&&p.y<=shape.y+shape.h;
  const nearHandle=(p,shape)=>shape.w>0&&Math.abs(p.x-shape.x-shape.w)<13&&
     Math.abs(p.y-shape.y-shape.h)<13;
  const updateNumber=(id,value)=>{
    const element=document.getElementById(id);
    if(element)element.value=String(value);
  };
  function adjust(p){
    const cfg=project.config,deltaX=(p.x-drag.start.x)*2,deltaY=(p.y-drag.start.y)*2;
    if(drag.kind==='circle'){
      if(drag.mode==='resize'){
        const size=Math.round(clamp(drag.size+Math.max(deltaX,deltaY),240,
                                   Math.min(1080,1080-drag.x,1920-drag.y)));
        cfg.circle_size=size;updateNumber('circleSize',size);
      }else{
        cfg.circle_x=Math.round(clamp(drag.x+deltaX,0,1080-drag.size));
        cfg.circle_y=Math.round(clamp(drag.y+deltaY,0,1920-drag.size));
        updateNumber('circleX',cfg.circle_x);updateNumber('circleY',cfg.circle_y);
      }
    }else if(drag.kind==='banner'){
      if(drag.mode==='resize'){
        cfg.banner_width=Math.round(clamp(drag.size+deltaX,80,Math.min(1080,1080-drag.x)));
        updateNumber('bannerWidth',cfg.banner_width);
      }else{
        cfg.banner_x=Math.round(clamp(drag.x+deltaX,0,1080-drag.size));
        cfg.banner_y=Math.round(clamp(drag.y+deltaY,-1920,1920));
        updateNumber('bannerX',cfg.banner_x);updateNumber('bannerY',cfg.banner_y);
      }
    }else if(drag.kind==='caption'){
      if(drag.mode==='resize'){
        cfg.caption_font_size=Math.round(clamp(drag.size+deltaX*.45+deltaY*.4,20,90));
        updateNumber('captionSize',cfg.caption_font_size);
      }else{
        cfg.caption_x=Math.round(clamp(drag.x+deltaX,0,1080));
        cfg.caption_y=Math.round(clamp(drag.y+deltaY,100,1800));
        updateNumber('captionX',cfg.caption_x);updateNumber('captionY',cfg.caption_y);
      }
    }else if(drag.kind==='background'){
      cfg.background_zoom=Math.round(clamp(drag.size+(deltaX-deltaY)/500,1,2)*100)/100;
      updateNumber('backgroundZoom',cfg.background_zoom);
      document.getElementById('backgroundZoomValue').textContent=
          cfg.background_zoom.toFixed(2)+'×';
    }
    drawGuides();
  }
  cvs.addEventListener('pointerdown',ev=>{
    if(!project)return;
    const p=point(ev),cfg=project.config;
    if(eyedropper){
      if(inBox(p,bannerShape)&&scratch.width){
        const data=sx.getImageData(Math.max(0,Math.min(scratch.width-1,Math.floor(p.x-bannerShape.x))),
                                 Math.max(0,Math.min(scratch.height-1,Math.floor(p.y-bannerShape.y))),1,1).data;
        const hex='#'+[data[0],data[1],data[2]].map(v=>v.toString(16).padStart(2,'0')).join('').toUpperCase();
        cfg.banner_chroma_color=hex;
        document.getElementById('bannerChromaColor').value=hex;
        cfg.banner_chroma_enabled=true;document.getElementById('bannerChroma').checked=true;
        alertStatus('Пипетка выбрала '+hex+' для удаления фона баннера.');
      }else alertStatus('Нажми на участок баннера внутри миниатюры',true);
      eyedropper=false;document.getElementById('eyeDropper').classList.remove('active');
      return;
    }
    if(p.x>=8&&p.x<=96&&p.y>=880&&p.y<=928){
      drag={kind:'background',mode:'resize',start:p,size:Number(cfg.background_zoom||1)};
    }else if(nearHandle(p,captionShape)){
      drag={kind:'caption',mode:'resize',start:p,size:Number(cfg.caption_font_size||48)};
    }else if(cfg.banner_enabled&&nearHandle(p,bannerShape)){
      drag={kind:'banner',mode:'resize',start:p,x:Number(cfg.banner_x??0),
            y:Number(cfg.banner_y??0),size:Number(cfg.banner_width||600)};
    }else if(nearHandle(p,circleShape)){
      drag={kind:'circle',mode:'resize',start:p,x:Number(cfg.circle_x??0),
            y:Number(cfg.circle_y??0),size:Number(cfg.circle_size||900)};
    }else if(inBox(p,captionShape)){
      drag={kind:'caption',mode:'move',start:p,
            x:Number(cfg.caption_x??540),y:Number(cfg.caption_y??820)};
    }else if(cfg.banner_enabled&&inBox(p,bannerShape)){
      drag={kind:'banner',mode:'move',start:p,x:Number(cfg.banner_x??0),
            y:Number(cfg.banner_y??0),size:Number(cfg.banner_width||600)};
    }else if(inBox(p,circleShape)){
      drag={kind:'circle',mode:'move',start:p,x:Number(cfg.circle_x??0),
            y:Number(cfg.circle_y??0),size:Number(cfg.circle_size||900)};
    }
    if(!drag)return;
    cvs.setPointerCapture(ev.pointerId);
    cvs.style.cursor=drag.mode==='resize'?'nwse-resize':'grabbing';
    ev.preventDefault();
  });
  cvs.addEventListener('pointermove',ev=>{
    if(!project)return;
    const p=point(ev);
    if(drag){adjust(p);return;}
    cvs.style.cursor=
      nearHandle(p,captionShape)||
      (project.config.banner_enabled&&nearHandle(p,bannerShape))||
      nearHandle(p,circleShape)||p.x<=96&&p.y>=880&&p.y<=928?
      'nwse-resize':inBox(p,captionShape)||inBox(p,bannerShape)||inBox(p,circleShape)?
      'grab':'default';
  });
  const finish=async()=>{
    if(!drag)return;
    drag=null;cvs.style.cursor='grab';
    try{await saveProject()}catch(e){alertStatus('Размер/позиция: '+e.message,true)}
  };
  cvs.addEventListener('pointerup',finish);
  cvs.addEventListener('pointercancel',finish);
  cvs.addEventListener('wheel',async ev=>{
    if(!project || ev.ctrlKey)return;
    const p=point(ev);
    if(inBox(p,circleShape)||inBox(p,captionShape)||inBox(p,bannerShape))return;
    ev.preventDefault();
    const cfg=project.config;
    cfg.background_zoom=Math.round(clamp(Number(cfg.background_zoom||1)+(ev.deltaY<0?.05:-.05),1,2)*100)/100;
    updateNumber('backgroundZoom',cfg.background_zoom);
    document.getElementById('backgroundZoomValue').textContent=cfg.background_zoom.toFixed(2)+'×';
    try{await saveProject()}catch(e){alertStatus(e.message,true)}
  },{passive:false});
  const dock=document.getElementById('liveDock');
  const grip=document.getElementById('liveResizeGrip');
  const MAX_WIDTH=700,MIN_WIDTH=260;
  const initial=Number(localStorage.getItem('viral_pro_preview_width'));
  if(Number.isFinite(initial)&&initial>=MIN_WIDTH){
    dock.style.width=Math.min(MAX_WIDTH,initial,window.innerWidth-24)+'px';
  }
  grip?.addEventListener('pointerdown',ev=>{
    ev.preventDefault();ev.stopPropagation();
    const rect=dock.getBoundingClientRect();
    const startX=ev.clientX,startY=ev.clientY,startWidth=rect.width,startHeight=rect.height;
    const move=e=>{
      dock.style.width=Math.round(clamp(startWidth+startX-e.clientX,MIN_WIDTH,
                                       Math.min(MAX_WIDTH,window.innerWidth-24)))+'px';
      dock.style.height=Math.round(clamp(startHeight+startY-e.clientY,300,
                                        Math.max(300,window.innerHeight-24)))+'px';
    };
    const finishResize=()=>{
      grip.removeEventListener('pointermove',move);
      grip.removeEventListener('pointerup',finishResize);
      grip.removeEventListener('pointercancel',finishResize);
      localStorage.setItem('viral_pro_preview_width',String(Math.round(dock.getBoundingClientRect().width)));
    };
    grip.setPointerCapture(ev.pointerId);
    grip.addEventListener('pointermove',move);
    grip.addEventListener('pointerup',finishResize);
    grip.addEventListener('pointercancel',finishResize);
  });
  document.getElementById('liveExpand')?.addEventListener('click',()=>{
    dock.classList.toggle('expanded');
    if(dock.classList.contains('expanded')){
      dock.style.removeProperty('height');
      dock.style.removeProperty('width');
    }else{
      const saved=Number(localStorage.getItem('viral_pro_preview_width')||360);
      dock.style.width=clamp(saved,MIN_WIDTH,MAX_WIDTH)+'px';
    }
  });
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