/* Telegram circle template: draggable border, progress and timestamp controls. */
(() => {
  const stage=document.getElementById('designPreview');
  const scale=document.getElementById('circleScale');
  if(!stage || !scale || typeof designState==='undefined')return;
  const defaults={
    visual_template:'classic',
    circle_border_width:9,circle_border_color:'#ffffff',
    progress_enabled:true,progress_style:'classic',progress_x:310,progress_y:1190,
    progress_width:460,progress_height:9,
    timecode_enabled:true,timecode_x:445,timecode_y:1220,
    timecode_font_size:32,output_fps:60,source_max_fps:true,smooth_motion:false,render_speed:'quiet'
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
    ['orbit_dots','Шаблон 2 · белый бегущий блик и загрузка'],
    ['orbit_arc','Шаблон 3 · вращающаяся световая дуга'],
    ['pulse_ring','Шаблон 4 · пульсирующее кольцо'],
    ['double_orbit','Шаблон 5 · двойная орбита'],
    ['dashed_spin','Шаблон 6 · вращающийся пунктир'],
    ['ripple','Шаблон 7 · расходящиеся волны'],
    ['comet_tail','Шаблон 8 · белая комета'],
    ['halo_breathe','Шаблон 9 · дышащий ореол'],
    ['twin_comets','Шаблон 10 · две встречные кометы'],
    ['starlight','Шаблон 11 · вращающиеся звёзды'],
    ['radar_sweep','Шаблон 12 · радарная дуга'],
  ]){
    const option=document.createElement('option');
    option.value=name;option.textContent=title;templateSelect.append(option);
  }
  templateRow.append(templateSelect);controls.append(templateRow);
  const templates={};
  window.setStudioTemplatePresets=presets=>{
    if(presets&&typeof presets==='object'){
      for(const key of ['classic','orbit_dots','orbit_arc','pulse_ring','double_orbit','dashed_spin','ripple','comet_tail','halo_breathe','twin_comets','starlight','radar_sweep']){
        if(presets[key])templates[key]={...presets[key]};
      }
    }
    sync();
  };
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
  let lastProgressStyle=designState.progress_style==='none'?'classic':(designState.progress_style||'classic');
  function checkbox(label,key){
    const row=document.createElement('label'),caption=document.createElement('span'),input=document.createElement('input');
    caption.textContent=label;input.type='checkbox';input.checked=!!designState[key];
    row.append(caption,input);controls.append(row);checks[key]=input;
    input.addEventListener('change',()=>{
      designState[key]=input.checked;
      if(key==='progress_enabled'){
        if(input.checked)designState.progress_style=lastProgressStyle;
        else if(designState.progress_style!=='none')lastProgressStyle=designState.progress_style;
      }
      updateDesignPreview();saveRenderDesign();
    });
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
  const progressRow=document.createElement('label');
  progressRow.textContent='Статус-бар / полоса воспроизведения';
  const progressStyleSelect=document.createElement('select');
  progressStyleSelect.id='studioProgressStyle';
  for(const [value,name] of [
    ['none','🚫 Скрыть полностью · без полосы'],
    ['classic','1 · Классическая полоса'],
    ['thin','2 · Тонкая минималистичная'],
    ['capsule','3 · Капсула со скруглениями'],
    ['segmented','4 · Сегментированная'],
    ['glow','5 · Светящаяся голубая'],
    ['dual','6 · Две параллельные линии'],
    ['pulse','7 · Пульсирующая'],
  ]){
    const option=document.createElement('option');
    option.value=value;option.textContent=name;progressStyleSelect.append(option);
  }
  progressRow.append(progressStyleSelect);controls.append(progressRow);
  progressStyleSelect.addEventListener('change',()=>{
    const style=progressStyleSelect.value;
    designState.progress_enabled=style!=='none';
    if(style!=='none')lastProgressStyle=style;
    designState.progress_style=style==='none'?lastProgressStyle:style;
    updateDesignPreview();saveRenderDesign();
  });
  checkbox('Показывать статус-бар','progress_enabled');
  slider('Ширина полосы','progress_width',100,1080,10);
  slider('Толщина полосы','progress_height',3,30);
  checkbox('Показывать таймкод','timecode_enabled');
  slider('Размер таймкода','timecode_font_size',18,64);
  const speedRow=document.createElement('label');
  speedRow.textContent='Нагрузка на компьютер';
  const speed=document.createElement('select');
  for(const [value,title] of [
    ['quiet','Тихий и быстрый · 30 FPS · 2 потока'],
    ['balanced','Сбалансированный · 30/60 FPS · 4 потока'],
    ['original','Без ограничений · максимальная нагрузка']
  ]){
    const option=document.createElement('option');
    option.value=value;option.textContent=title;speed.append(option);
  }
  speedRow.append(speed);controls.append(speedRow);
  const speedHint=document.createElement('span');
  speedHint.className='hint';controls.append(speedHint);
  speed.addEventListener('change',()=>{
    designState.render_speed=speed.value;
    updateDesignPreview();saveRenderDesign();
  });
  const fpsRow=document.createElement('label'),fps=document.createElement('select');
  fpsRow.textContent='Частота кадров MP4';
  for(const n of [30,60,120]){
    const opt=document.createElement('option');opt.value=String(n);opt.textContent=n+' FPS';fps.append(opt);
  }
  fps.value=String(designState.output_fps);fpsRow.append(fps);controls.append(fpsRow);
  fps.addEventListener('change',()=>{designState.output_fps=Number(fps.value);saveRenderDesign();});
  checkbox('Авто FPS: сохранить все исходные кадры баннера (до 120 FPS)','source_max_fps');
  checkbox('Оптическая интерполяция кружка и видеофона','smooth_motion');
  const ultra=document.createElement('button');
  ultra.type='button';ultra.id='studioUltraQuality';
  ultra.textContent='🚀 Максимум: улучшить кружок + плавность + исходный FPS баннера';
  ultra.className='studio-ultra-quality';
  controls.append(ultra);
  ultra.addEventListener('click',()=>{
    Object.assign(designState,{
      quality_preset:'maximum',render_speed:'original',output_fps:60,
      source_max_fps:true,smooth_motion:true,chromakey_mode:'advanced',
      chromakey_enabled:true,
    });
    const preset=document.getElementById('tgQualityPreset');
    if(preset)preset.value='maximum';
    const chroma=document.getElementById('chromaAdvanced');
    if(chroma)chroma.checked=true;
    updateDesignPreview();saveRenderDesign();
  });
  const tip=document.createElement('span');
  tip.className='hint';tip.textContent='Кнопка «Максимум» включает CRF 14, оптическое создание промежуточных кадров кружка и видеофона, авто FPS до 120 и усиленный хромакей. Видео-баннер не интерполируется: его исходные кадры сохраняются, дополнительные кадры удерживаются. Это медленный рендер и большой MP4; из плохого источника нельзя восстановить несуществующие детали.';
  controls.append(tip);
  scale.closest('.design-field').insertAdjacentElement('afterend',controls);

  const progress=document.createElement('div'),fill=document.createElement('div'),grip=document.createElement('span');
  progress.className='studio-progress-edit';progress.id='studioProgressDrag';
  progress.title='Перемести прогресс; потяни маркер для размера';
  fill.className='studio-progress-fill';grip.className='studio-progress-grip';grip.dataset.resize='progress';
  progress.append(fill,grip);
  const orbitLayer=document.createElement('div');
  orbitLayer.className='studio-orbit-ring';
  orbitLayer.setAttribute('aria-hidden','true');
  stage.append(orbitLayer);
  // Live approximation of the exported animated ring. The MP4 is drawn by
  // FFmpeg; these CSS rings are only an interactive visual guide.
  const ringStyles=document.createElement('style');
  ringStyles.textContent=`
    .studio-orbit-ring.studio-ring-orbit_arc {background:conic-gradient(transparent 0deg 185deg,#fff 195deg 310deg,transparent 325deg 360deg);border-radius:50%;animation:studioSpin 3s linear infinite;mask:radial-gradient(farthest-side,transparent calc(100% - 5px),#000 calc(100% - 4px));}
    .studio-orbit-ring.studio-ring-pulse_ring {background:none;border:3px solid white;border-radius:50%;animation:studioPulse 1.5s ease-in-out infinite;mask:none;-webkit-mask:none;}
    .studio-orbit-ring.studio-ring-double_orbit {background:conic-gradient(#fff 0 90deg,transparent 90deg 180deg,#fff 180deg 270deg,transparent 270deg);border-radius:50%;animation:studioSpin 5s linear infinite;mask:radial-gradient(farthest-side,transparent calc(100% - 5px),#000 calc(100% - 4px));}
    .studio-orbit-ring.studio-ring-dashed_spin {background:repeating-conic-gradient(#fff 0 12deg,transparent 12deg 30deg);border-radius:50%;animation:studioSpin 8s linear infinite;mask:radial-gradient(farthest-side,transparent calc(100% - 5px),#000 calc(100% - 4px));}
    .studio-orbit-ring.studio-ring-ripple {background:none;border:3px solid white;border-radius:50%;animation:studioRipple 1.7s ease-out infinite;mask:none;-webkit-mask:none;}
    .studio-orbit-ring.studio-ring-comet_tail {background:conic-gradient(transparent 0 190deg,#ffffff55 215deg,#fff 320deg,transparent 342deg);border-radius:50%;animation:studioSpin 4s linear infinite;mask:radial-gradient(farthest-side,transparent 91%,#000 92% 94%,transparent 95%);}
    .studio-orbit-ring.studio-ring-halo_breathe {background:none;border:3px solid #c3efff;border-radius:50%;box-shadow:0 0 10px #9feaff88,0 0 22px #9feaff55;animation:studioPulse 2.5s ease-in-out infinite;mask:none;-webkit-mask:none;}
    .studio-orbit-ring.studio-ring-twin_comets {background:conic-gradient(#fff 0 85deg,transparent 105deg 180deg,#9feaff 180deg 265deg,transparent 285deg 360deg);border-radius:50%;animation:studioSpin 6s linear infinite;mask:radial-gradient(farthest-side,transparent 91%,#000 92% 94%,transparent 95%);}
    .studio-orbit-ring.studio-ring-starlight {background:repeating-conic-gradient(#fff 0 4deg,transparent 4deg 22.5deg);border-radius:50%;animation:studioSpin 9s linear infinite;mask:radial-gradient(farthest-side,transparent 91%,#000 92% 94%,transparent 95%);}
    .studio-orbit-ring.studio-ring-radar_sweep {background:conic-gradient(transparent 0 175deg,#9feaff55 205deg,#9feaff 295deg,#fff 325deg,transparent 340deg);border-radius:50%;animation:studioSpin 5s linear infinite;mask:radial-gradient(farthest-side,transparent 91%,#000 92% 94%,transparent 95%);}
    @keyframes studioSpin {to{transform:rotate(360deg)}}
    @keyframes studioPulse {50%{transform:scale(1.055);opacity:.45}}
    @keyframes studioRipple {0%{transform:scale(.98);opacity:1}100%{transform:scale(1.09);opacity:.1}}
  `;
  document.head.append(ringStyles);

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
    const shown=designState.progress_style==='segmented'?
      Math.ceil(percent*14)/14:percent;
    fill.style.width=(shown*100)+'%';
    timecode.textContent=labelTime(current)+' / '+labelTime(duration);
    // The second style shows the existing backdrop alone for 0.38 seconds;
    // circle, white moving highlight, progress and banner then fade in.
    stage.classList.toggle('studio-intro-frame',
      designState.visual_template!=='classic' &&
      sample.readyState>=2 && !sample.paused && current<0.38);
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
    speed.value=state.render_speed||'quiet';
    const quiet=speed.value==='quiet'&&state.quality_preset!=='maximum';
    fps.disabled=quiet;
    checks.smooth_motion.disabled=speed.value!=='original'&&state.quality_preset!=='maximum';
    speedHint.textContent=state.quality_preset==='maximum'?
      'Максимум: H.264 CRF 14 без ограничения битрейта, очистка шума и резкость только на кружке, авто 60–120 FPS. Оптическая интерполяция создаёт промежуточные кадры при включённой галочке. Видео-баннер не размывается фильтрами.':
      quiet?
      'Эффективно 30 FPS, H.264 veryfast, без тяжёлых фильтров и интерполяции. '+
      'Размер 1080×1920, фон, кружок, обводка, прогресс и баннер сохраняются.':
      speed.value==='balanced'?
      'Оставляет выбранные FPS и качество, но ограничивает FFmpeg четырьмя потоками.':
      'Все фильтры и выбранные FPS без ограничения потоков — максимальная нагрузка.';
    const orbit=state.visual_template!=='classic';
    templateSelect.value=state.visual_template||'classic';
    checks.timecode_enabled.disabled=orbit;
    progress.className='studio-progress-edit'+(orbit?' studio-progress-loading':'')+
      ' studio-progress-style-'+(state.progress_style||'classic');
    orbitLayer.hidden=!orbit;
    orbitLayer.className='studio-orbit-ring studio-ring-'+state.visual_template;
    const margin=36;
    orbitLayer.style.left=((state.circle_x-margin)/1080*100)+'%';
    orbitLayer.style.top=((state.circle_y-margin)/1920*100)+'%';
    orbitLayer.style.width=((state.circle_size+2*margin)/1080*100)+'%';
    orbitLayer.style.height=((state.circle_size+2*margin)/1920*100)+'%';
    const scaleFactor=stage.getBoundingClientRect().width/1080;
    for(const circle of [sample,document.getElementById('designCirclePlaceholder')]){
      circle.style.boxSizing='border-box';
      circle.style.border=(state.circle_border_width*scaleFactor)+'px solid '+state.circle_border_color;
    }
    if(state.progress_enabled && state.progress_style && state.progress_style!=='none')
      lastProgressStyle=state.progress_style;
    progressStyleSelect.value=state.progress_enabled?(state.progress_style||'classic'):'none';
    ranges.progress_width.input.disabled=!state.progress_enabled;
    ranges.progress_height.input.disabled=!state.progress_enabled;
    progress.hidden=!state.progress_enabled||state.progress_style==='none';
    progress.style.left=(state.progress_x/1080*100)+'%';
    progress.style.width=(state.progress_width/1080*100)+'%';
    const barHeight=state.progress_style==='thin'?Math.min(4,state.progress_height):state.progress_height;
    const offsetY=(state.progress_height-barHeight)/2;
    progress.style.top=((state.progress_y+offsetY)/1920*100)+'%';
    progress.style.height=(barHeight/1920*100)+'%';
    timecode.hidden=orbit||!state.timecode_enabled;
    timecode.style.left=(state.timecode_x/1080*100)+'%';
    timecode.style.top=(state.timecode_y/1920*100)+'%';
    timecode.style.fontSize=Math.max(9,state.timecode_font_size*scaleFactor)+'px';
    playback();
  }
  const previousUpdate=updateDesignPreview;
  updateDesignPreview=function(){previousUpdate();sync();};
  if(window.studioLoadedPresets)window.setStudioTemplatePresets(window.studioLoadedPresets);
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
