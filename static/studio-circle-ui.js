/* Telegram circle template: draggable border, progress and timestamp controls. */
(() => {
  const stage=document.getElementById('designPreview');
  const scale=document.getElementById('circleScale');
  if(!stage || !scale || typeof designState==='undefined')return;
  const defaults={
    visual_template:'classic',
    circle_border_width:9,circle_border_color:'#ffffff',
    progress_enabled:true,progress_x:310,progress_y:1190,
    progress_width:460,progress_height:9,
    timecode_enabled:true,timecode_x:445,timecode_y:1220,
    timecode_font_size:32,output_fps:60,smooth_motion:false
  };
  for(const key in defaults)if(designState[key]===undefined)designState[key]=defaults[key];
  const controls=document.createElement('div');
  controls.className='studio-effects-panel';
  const title=document.createElement('strong');
  title.textContent='Обводка · прогресс · таймкод · плавность';
  controls.append(title);
  const templateRow=document.createElement('label');
  templateRow.textContent='Оформление кружка';
  const templateSelect=document.createElement('select');
  for(const [name,title] of [
    ['classic','Шаблон 1 · обычная обводка и полоска'],
    ['orbit_dots','Шаблон 2 · вращающаяся обводка и точки (без времени)'],
  ]){
    const option=document.createElement('option');
    option.value=name;option.textContent=title;templateSelect.append(option);
  }
  templateRow.append(templateSelect);controls.append(templateRow);
  const templates={};
  window.setStudioTemplatePresets=presets=>{
    if(presets&&typeof presets==='object'){
      for(const key of ['classic','orbit_dots']){
        if(presets[key])templates[key]={...presets[key]};
      }
    }
    sync();
  };
  if(window.studioLoadedPresets)window.setStudioTemplatePresets(window.studioLoadedPresets);
  templateSelect.addEventListener('change',()=>{
    const old=designState.visual_template||'classic';
    templates[old]={...designState,visual_template:old};
    const which=templateSelect.value;
    const next=templates[which]||{
      ...designState,visual_template:which,
      timecode_enabled:which==='classic'?designState.timecode_enabled:false
    };
    Object.assign(designState,next,{visual_template:which});
    updateDesignPreview();saveRenderDesign();
  });

  const checks={};const ranges={};
  function checkbox(label,key){
    const row=document.createElement('label'),caption=document.createElement('span'),input=document.createElement('input');
    caption.textContent=label;input.type='checkbox';input.checked=!!designState[key];
    row.append(caption,input);controls.append(row);checks[key]=input;
    input.addEventListener('change',()=>{designState[key]=input.checked;updateDesignPreview();saveRenderDesign();});
  }
  function slider(label,key,min,max,step=1){
    const box=document.createElement('div'),caption=document.createElement('label'),text=document.createElement('span');
    caption.append(document.createTextNode(label+': '),text);
    const input=document.createElement('input');
    input.type='range';input.min=min;input.max=max;input.step=step;input.value=designState[key];
    box.append(caption,input);controls.append(box);ranges[key]={input,text};
    input.addEventListener('input',()=>{designState[key]=Number(input.value);updateDesignPreview();});
    input.addEventListener('change',saveRenderDesign);
  }
  slider('Толщина обводки', 'circle_border_width',0,48);
  const colorRow=document.createElement('label'),color=document.createElement('input');
  colorRow.textContent='Цвет обводки';color.type='color';color.value=designState.circle_border_color;
  colorRow.append(color);controls.append(colorRow);
  color.addEventListener('input',()=>{designState.circle_border_color=color.value;updateDesignPreview();});
  color.addEventListener('change',saveRenderDesign);
  checkbox('Показывать прогресс-бар','progress_enabled');
  slider('Ширина полосы','progress_width',100,1080,10);
  slider('Толщина полосы','progress_height',3,30);
  checkbox('Показывать таймкод','timecode_enabled');
  slider('Размер таймкода','timecode_font_size',18,64);
  const fpsRow=document.createElement('label'),fps=document.createElement('select');
  fpsRow.textContent='Частота кадров MP4';
  for(const n of [30,60]){
    const opt=document.createElement('option');opt.value=String(n);opt.textContent=n+' FPS';fps.append(opt);
  }
  fps.value=String(designState.output_fps);fpsRow.append(fps);controls.append(fpsRow);
  fps.addEventListener('change',()=>{designState.output_fps=Number(fps.value);saveRenderDesign();});
  checkbox('Интерполяция движения (медленный рендер)','smooth_motion');
  const tip=document.createElement('span');
  tip.className='hint';tip.textContent='Перетаскивай полоску и таймкод в миниатюре. Правый маркер меняет ширину полосы. 60 FPS — вывод, интерполяция — создание дополнительных кадров.';
  controls.append(tip);
  scale.closest('.design-field').insertAdjacentElement('afterend',controls);

  const progress=document.createElement('div'),fill=document.createElement('div'),grip=document.createElement('span');
  progress.className='studio-progress-edit';progress.id='studioProgressDrag';
  progress.title='Перемести прогресс; потяни маркер для размера';
  fill.className='studio-progress-fill';grip.className='studio-progress-grip';grip.dataset.resize='progress';
  progress.append(fill,grip);
  const dotsRow=document.createElement('div');
  dotsRow.className='studio-dot-row';progress.append(dotsRow);
  const orbitLayer=document.createElement('div');
  orbitLayer.className='studio-orbit-ring';
  orbitLayer.setAttribute('aria-hidden','true');
  stage.append(orbitLayer);
  const timecode=document.createElement('div');timecode.id='studioTimecodeDrag';
  timecode.className='studio-timecode-edit';timecode.title='Перетаскивай таймкод';
  stage.append(progress,timecode);

  const sample=document.getElementById('designCircleVideo');
  const clamp=(n,lo,hi)=>Math.max(lo,Math.min(hi,Math.round(Number(n)||0)));
  const labelTime=s=>{
    const n=Math.max(0,Math.floor(Number(s)||0));
    return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0');
  };
  function playback(){
    const duration=Number.isFinite(sample.duration)&&sample.duration>0?sample.duration:30;
    const current=Number.isFinite(sample.currentTime)?sample.currentTime:0;
    const percent=Math.max(0,Math.min(1,current/duration));
    fill.style.width=(percent*100)+'%';
    timecode.textContent=labelTime(current)+' / '+labelTime(duration);
    const dots=[...dotsRow.children];
    const completed=Math.round(percent*dots.length);
    for(let i=0;i<dots.length;i++){
      dots[i].classList.toggle('filled',i<completed);
      dots[i].classList.toggle('head',completed>0&&i===completed-1);
    }
  }
  function sync(){
    const state=designState;
    state.circle_border_width=clamp(state.circle_border_width,0,48);
    state.progress_width=clamp(state.progress_width,100,1080);
    state.progress_height=clamp(state.progress_height,3,30);
    state.progress_x=clamp(state.progress_x,0,1080-state.progress_width);
    state.progress_y=clamp(state.progress_y,0,1920-state.progress_height);
    state.timecode_font_size=clamp(state.timecode_font_size,18,64);
    state.timecode_x=clamp(state.timecode_x,0,900);
    state.timecode_y=clamp(state.timecode_y,0,1920-state.timecode_font_size-4);
    for(const key in ranges){
      ranges[key].input.value=state[key];
      ranges[key].text.textContent=state[key]+' px';
    }
    for(const key in checks)checks[key].checked=!!state[key];
    color.value=state.circle_border_color;
    fps.value=String(state.output_fps);
    const orbit=state.visual_template==='orbit_dots';
    templateSelect.value=orbit?'orbit_dots':'classic';
    checks.timecode_enabled.disabled=orbit;
    progress.classList.toggle('studio-progress-dots',orbit);
    orbitLayer.hidden=!orbit;
    const margin=36;
    orbitLayer.style.left=((state.circle_x-margin)/1080*100)+'%';
    orbitLayer.style.top=((state.circle_y-margin)/1920*100)+'%';
    orbitLayer.style.width=((state.circle_size+2*margin)/1080*100)+'%';
    orbitLayer.style.height=((state.circle_size+2*margin)/1920*100)+'%';
    const dotCount=Math.max(9,Math.min(28,Math.round(state.progress_width/27)));
    if(dotsRow.children.length!==dotCount){
      dotsRow.replaceChildren();
      for(let i=0;i<dotCount;i++){
        const dot=document.createElement('span');
        dot.className='studio-dot';dotsRow.append(dot);
      }
    }
    const scaleFactor=stage.getBoundingClientRect().width/1080;
    for(const circle of [sample,document.getElementById('designCirclePlaceholder')]){
      circle.style.boxSizing='border-box';
      circle.style.border=(state.circle_border_width*scaleFactor)+'px solid '+state.circle_border_color;
    }
    progress.hidden=!state.progress_enabled;
    progress.style.left=(state.progress_x/1080*100)+'%';
    progress.style.top=(state.progress_y/1920*100)+'%';
    progress.style.width=(state.progress_width/1080*100)+'%';
    progress.style.height=(state.progress_height/1920*100)+'%';
    timecode.hidden=orbit||!state.timecode_enabled;
    timecode.style.left=(state.timecode_x/1080*100)+'%';
    timecode.style.top=(state.timecode_y/1920*100)+'%';
    timecode.style.fontSize=Math.max(9,state.timecode_font_size*scaleFactor)+'px';
    playback();
  }
  const previousUpdate=updateDesignPreview;
  updateDesignPreview=function(){previousUpdate();sync();};
  sync();
  sample.addEventListener('timeupdate',playback);
  sample.addEventListener('loadedmetadata',playback);
  setInterval(playback,150);
  window.addEventListener('resize',sync);

  function draggable(layer,prefix,allowResize){
    let active=null;
    layer.addEventListener('pointerdown',e=>{
      e.preventDefault();e.stopPropagation();
      const rect=stage.getBoundingClientRect();
      active={
        id:e.pointerId,resize:allowResize&&!!e.target.closest('[data-resize="progress"]'),
        px:e.clientX,py:e.clientY,x:designState[prefix+'_x'],
        y:designState[prefix+'_y'],width:designState.progress_width,
        w:rect.width,h:rect.height
      };
      layer.setPointerCapture(e.pointerId);
    });
    layer.addEventListener('pointermove',e=>{
      if(!active||active.id!==e.pointerId)return;
      const dx=(e.clientX-active.px)*1080/active.w;
      const dy=(e.clientY-active.py)*1920/active.h;
      if(active.resize){
        designState.progress_width=clamp(active.width+dx,100,1080-designState.progress_x);
      }else{
        designState[prefix+'_x']=active.x+dx;
        designState[prefix+'_y']=active.y+dy;
      }
      updateDesignPreview();
    });
    const finish=e=>{
      if(!active||active.id!==e.pointerId)return;
      active=null;saveRenderDesign();
    };
    layer.addEventListener('pointerup',finish);
    layer.addEventListener('pointercancel',finish);
  }
  draggable(progress,'progress',true);
  draggable(timecode,'timecode',false);
})();
