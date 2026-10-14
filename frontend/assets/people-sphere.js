/* A dependency-free 3D sphere: shared projection for portraits and their stems. */
(function () {
  const el = id => document.getElementById(id);
  const sphere = el('sphere');
  const canvas = el('connections');
  const ctx = canvas.getContext('2d');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const TAU = Math.PI * 2;
  let nodes = [], selected = null, hovered = null, listMode = false;
  let width = 0, height = 0, radius = 0, yaw = .35, pitch = -.16;
  let targetYaw = yaw, targetPitch = pitch, zoom = 1, targetZoom = 1;
  let autoRotate = !reducedMotion.matches, dragging = false, moved = false;
  let lastTime = 0, frame = 0, visible = true, interactionTime = 0;
  let morphing = false, morphVersion = 0, morphFrame = 0, viewAnimations = [];
  let onSelect = () => {}, photoURL = null;
  const pointers = new Map();
  let lastPinch = 0;
  const clamp = (v, low, high) => Math.min(high, Math.max(low, v));
  const shortestAngle = (from, to) => from + ((to - from + Math.PI) % TAU + TAU) % TAU - Math.PI;
  function stemLengthFor(id) {
    // Keep each person's stem length stable when photos or ordering change.
    let hash = 2166136261;
    for (const character of String(id)) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619) >>> 0;
    return .76 + (hash / 4294967296) * .4;
  }

  // Deterministic star positions avoid changing the sky on each refresh.
  let seed = 73;
  const random = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  const stars = document.createDocumentFragment();
  for (let i = 0; i < 180; i++) {
    const star = document.createElement('span');
    star.className = 'star' + (i % 12 === 0 ? ' gold' : '');
    const size = i % 17 === 0 ? 2 : 1;
    star.style.cssText = `left:${random()*100}%;top:${random()*100}%;width:${size}px;height:${size}px;--alpha:${.18+random()*.48};--time:${3+random()*6}s;animation-delay:-${random()*7}s`;
    stars.append(star);
  }
  el('stars').append(stars);

  function sizeScene() {
    if (sphere.hidden) return;
    width = sphere.clientWidth; height = sphere.clientHeight;
    radius = Math.min(width * .33, height * .33, 250);
    const dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
    wake();
  }

  function project(point) {
    const cosY = Math.cos(yaw), sinY = Math.sin(yaw);
    const cosP = Math.cos(pitch), sinP = Math.sin(pitch);
    const x = point.x * cosY + point.z * sinY;
    const z = -point.x * sinY + point.z * cosY;
    const y = point.y * cosP - z * sinP;
    const depth = point.y * sinP + z * cosP;
    const perspective = 1 / (1 - depth * .28);
    return {x: width/2 + x*radius*perspective*zoom, y: height*.47 + y*radius*perspective*zoom, z: depth, perspective};
  }

  function avoidCouple(node, p, scale, coreRadius) {
    const dx = p.x-width/2, dy = p.y-height*.47;
    const distance = Math.hypot(dx, dy);
    const reach = coreRadius + 24*scale + 10*zoom;
    const influence = reach*2;
    if (distance >= influence) { node.avoidSide = 0; return p; }
    // Keep one passing side until the portrait leaves the influence area.
    // A lateral offset lets its original path keep moving through the center.
    if (!node.avoidSide) node.avoidSide = Math.sign(Math.abs(dx) > .5 ? dx : node.point.x) || 1;
    const proximity = 1-distance/influence;
    const strength = proximity*proximity*(3-2*proximity);
    const shift = reach*.95*strength;
    // No minimum radius: displacement grows and fades smoothly with proximity.
    return {...p, x:p.x+node.avoidSide*shift, y:p.y+node.avoidBias*shift};
  }

  function draw(time, force = false) {
    frame = 0;
    if (!force && (document.hidden || !visible || listMode || morphing)) { lastTime = 0; return; }
    const dt = lastTime ? Math.min(time-lastTime, 50) : 16;
    lastTime = time;
    const ease = force || reducedMotion.matches ? 1 : 1-Math.exp(-dt/110);
    if (!force && autoRotate && !dragging && !selected && !hovered && time-interactionTime > 1600) targetYaw += dt*.000055;
    yaw += (targetYaw-yaw)*ease; pitch += (targetPitch-pitch)*ease; zoom += (targetZoom-zoom)*ease;
    ctx?.clearRect(0, 0, width, height);
    const active = selected || hovered;
    const density = clamp(Math.sqrt(65/Math.max(nodes.length, 1)), .5, 1);
    const coreRadius = el('couple').offsetWidth*.5*zoom;
    for (const node of nodes) {
      let p = project(node.point);
      const isActive = node.person.id === active;
      const extraScale = isActive ? (selected ? 2.65 : 1.85) : 1;
      node.scale += (extraScale-node.scale)*ease;
      // Use camera perspective for size, with stronger depth contrast in the full sphere.
      // Click focus already magnifies the portrait, so keep its framing more restrained.
      const depthScale = .85 * p.perspective ** (node.person.id === selected ? 1 : 2);
      const scale = depthScale*zoom*(isActive ? Math.max(density, .9) : density)*node.scale;
      // Click focus intentionally occupies the center; other faces receive a soft offset.
      if (node.person.id !== selected) p = avoidCouple(node, p, scale, coreRadius);
      else node.avoidSide = 0;
      node.depth = p.z;
      // Top half of the button is the face; its lower half contains the count.
      node.button.style.transform = `translate(${p.x-31}px,${p.y-24}px) scale(${scale})`;
      node.button.style.transformOrigin = '31px 24px';
      node.button.style.zIndex = isActive ? '1800' : String(1000+Math.round(p.z*400));
      node.button.style.opacity = active && !isActive ? '.22' : String(.45+(p.z+1)*.275);
      node.button.classList.toggle('active', isActive);
      node.count.style.fontSize = `${10/node.scale}px`;
      node.count.style.lineHeight = `${14/node.scale}px`;
      const onscreen = p.x > -35 && p.x < width+35 && p.y > -35 && p.y < height+35;
      node.button.style.visibility = onscreen ? 'visible' : 'hidden';
      if (ctx) {
        const alpha = active ? (isActive ? .85 : .055) : .10+(p.z+1)*.14;
        const gradient = ctx.createLinearGradient(width/2, height*.47, p.x, p.y);
        gradient.addColorStop(0, `rgba(230,211,166,${alpha*.7})`);
        gradient.addColorStop(1, isActive ? `rgba(241,218,163,${alpha})` : `rgba(147,186,221,${alpha})`);
        ctx.strokeStyle = gradient; ctx.lineWidth = isActive ? 1.3 : .65;
        ctx.beginPath(); ctx.moveTo(width/2, height*.47); ctx.lineTo(p.x, p.y); ctx.stroke();
        if (isActive || node.index % 7 === 0) {
          ctx.fillStyle = isActive ? '#efd7a5' : '#abc9de';
          ctx.globalAlpha = alpha;
          ctx.beginPath(); ctx.arc(width/2+(p.x-width/2)*.63, height*.47+(p.y-height*.47)*.63, 1.3, 0, TAU); ctx.fill(); ctx.globalAlpha = 1;
        }
      }
    }
    el('couple').style.opacity = selected ? '.15' : hovered ? '.65' : '1';
    el('couple').style.transform = `scale(${zoom})`;
    // Stop idle rendering when rotation is paused or reduced motion is requested.
    const settling = Math.abs(targetYaw-yaw)+Math.abs(targetPitch-pitch)+Math.abs(targetZoom-zoom) > .0001 || nodes.some(n => Math.abs(n.scale-((n.person.id === active) ? (selected ? 2.65 : 1.85) : 1)) > .001);
    if ((autoRotate && nodes.length && !selected && !hovered) || settling || dragging) wake();
  }

  function wake() { if (!frame && !document.hidden && visible && !listMode && !morphing) frame = requestAnimationFrame(draw); }
  function reset() {
    selected = null; hovered = null; targetZoom = 1;
    sphere.parentElement.classList.remove('is-focused');
    el('focusCard').hidden = true;
    for (const node of nodes) node.button.setAttribute('aria-pressed', 'false');
    interactionTime = performance.now(); wake();
  }
  function focus(id) {
    if (listMode || morphing) return;
    const node = nodes.find(n => n.person.id === id);
    if (!node) return;
    selected = id; hovered = null;
    sphere.parentElement.classList.add('is-focused');
    const p = node.point;
    targetYaw = shortestAngle(yaw, Math.atan2(-p.x, p.z));
    targetPitch = shortestAngle(pitch, Math.atan2(p.y, Math.hypot(p.x, p.z)));
    targetZoom = 1.15;
    el('focusCard').hidden = false;
    el('focusCount').textContent = `在 ${node.person.count} 張婚禮照片裡，找到你的身影。`;
    for (const n of nodes) n.button.setAttribute('aria-pressed', String(n === node));
    wake();
  }
  function render(people) {
    finishMorph();
    const previousSelected = selected;
    nodes = [];
    const fragment = document.createDocumentFragment();
    people.forEach((person, index) => {
      // Fibonacci distribution gives every face a distinct place on the sphere.
      const y = 1-2*(index+.5)/people.length;
      const r = Math.sqrt(1-y*y), theta = index*Math.PI*(3-Math.sqrt(5));
      const stemLength = stemLengthFor(person.id);
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'sphere-person'; button.dataset.id = person.id;
      button.setAttribute('aria-label', `聚焦第 ${index+1} 個人物，${person.count} 張照片`);
      button.setAttribute('aria-pressed', 'false');
      const wrap = document.createElement('span'); wrap.className = 'avatar-wrap';
      const img = document.createElement('img'); img.src = WeddingAPI.url(person.thumbnail); img.alt = '人物大頭照'; img.draggable = false;
      img.onerror = () => button.classList.add('broken');
      const fallback = document.createElement('span'); fallback.className = 'avatar-fallback'; fallback.textContent = '✧'; fallback.setAttribute('aria-hidden', 'true');
      wrap.append(img, fallback);
      const count = document.createElement('span'); count.className = 'count'; count.textContent = `${person.count} 張`;
      button.append(wrap, count);
      button.addEventListener('pointerenter', event => { if (event.pointerType === 'touch' || dragging || selected || listMode || morphing) return; hovered = person.id; wake(); });
      button.addEventListener('pointerleave', () => { if (hovered === person.id) { hovered = null; interactionTime = performance.now(); wake(); } });
      button.addEventListener('focus', () => { if (!selected && !listMode && !morphing) { hovered = person.id; wake(); } });
      button.addEventListener('blur', () => { if (hovered === person.id) { hovered = null; wake(); } });
      button.addEventListener('click', event => { if (morphing || (!listMode && moved && event.detail !== 0)) return; if (listMode) onSelect(person.id); else focus(person.id); });
      nodes.push({person, index, point:{x: r*Math.cos(theta)*stemLength, y:y*stemLength, z:r*Math.sin(theta)*stemLength}, button, count, scale:1, avoidSide:0, avoidBias:((stemLength-.76)/.4-.5)*.32});
      fragment.append(button);
    });
    el('spherePeople').replaceChildren(); el('people').replaceChildren();
    (listMode ? el('people') : el('spherePeople')).append(fragment);
    for (const node of nodes) applyNodeView(node);
    el('personTotal').textContent = people.length ? `${people.length} 位相遇的人` : '';
    sphere.classList.toggle('has-empty', !people.length);
    hovered = null;
    if (!listMode && previousSelected && people.some(p => p.id === previousSelected)) focus(previousSelected); else reset();
    sizeScene();
  }
  function applyNodeView(node) {
    node.button.classList.toggle('sphere-person', !listMode);
    node.button.classList.toggle('person', listMode);
    node.button.classList.remove('active');
    node.button.removeAttribute('style');
    node.button.setAttribute('aria-label', listMode ? `查看人物相簿，${node.person.count} 張照片` : `聚焦第 ${node.index+1} 個人物，${node.person.count} 張照片`);
    if (listMode) {
      node.button.style.transformOrigin = '50% 33px';
      node.count.style.fontSize = '11px'; node.count.style.lineHeight = '14px';
    }
  }
  function finishMorph(version = morphVersion) {
    if (!morphing || version !== morphVersion) return;
    morphing = false;
    cancelAnimationFrame(morphFrame); morphFrame = 0;
    for (const animation of viewAnimations) animation.cancel();
    viewAnimations = [];
    sphere.parentElement.classList.remove('is-morphing');
    sphere.parentElement.removeAttribute('aria-busy');
    for (const node of nodes) node.button.removeAttribute('tabindex');
    sphere.hidden = listMode; sphere.inert = listMode;
    lastTime = 0;
    if (!listMode) { draw(performance.now(), true); wake(); }
  }
  function drawMorphConnections(version) {
    if (!morphing || version !== morphVersion) return;
    if (ctx) {
      const bounds = sphere.getBoundingClientRect();
      ctx.clearRect(0, 0, width, height);
      for (const node of nodes) {
        const avatar = node.button.querySelector('.avatar-wrap').getBoundingClientRect();
        const x = avatar.x+avatar.width/2-bounds.x, y = avatar.y+avatar.height/2-bounds.y;
        const alpha = .1+((node.depth ?? 0)+1)*.14;
        const gradient = ctx.createLinearGradient(width/2, height*.47, x, y);
        gradient.addColorStop(0, `rgba(230,211,166,${alpha*.7})`);
        gradient.addColorStop(1, `rgba(147,186,221,${alpha})`);
        ctx.strokeStyle = gradient; ctx.lineWidth = .65;
        ctx.beginPath(); ctx.moveTo(width/2, height*.47); ctx.lineTo(x, y); ctx.stroke();
      }
    }
    morphFrame = requestAnimationFrame(() => drawMorphConnections(version));
  }
  function setList(value) {
    if (value === listMode) return;
    const portraits = new Map(nodes.map(node => [node.person.id, {
      rect:node.button.querySelector('.avatar-wrap').getBoundingClientRect(),
      opacity:getComputedStyle(node.button).opacity,
    }]));
    const decorations = [canvas, el('couple'), ...sphere.querySelectorAll('.orbit, .couple-title')];
    const decorationOpacity = decorations.map(element => getComputedStyle(element).opacity);
    finishMorph();
    cancelAnimationFrame(frame); frame = 0;
    morphing = true;
    const version = ++morphVersion;
    reset();
    targetYaw = yaw; targetPitch = pitch; zoom = targetZoom = 1;
    for (const id of pointers.keys()) if (sphere.hasPointerCapture(id)) sphere.releasePointerCapture(id);
    pointers.clear(); dragging = moved = false; sphere.classList.remove('dragging');
    listMode = value; sphere.hidden = false; sphere.inert = value; el('listPanel').hidden = !value;
    sphere.parentElement.classList.toggle('is-list', value);
    sphere.parentElement.classList.add('is-morphing');
    sphere.parentElement.setAttribute('aria-busy', 'true');
    el('focusCard').hidden = true;
    el('sphereView').classList.toggle('active', !value); el('listView').classList.toggle('active', value);
    el('sphereView').setAttribute('aria-pressed', String(!value)); el('listView').setAttribute('aria-pressed', String(value));
    const destination = value ? el('people') : el('spherePeople');
    const fragment = document.createDocumentFragment();
    for (const node of nodes) {
      node.scale = 1; applyNodeView(node); fragment.append(node.button);
      node.button.setAttribute('tabindex', '-1');
    }
    destination.append(fragment);
    sizeScene();
    if (!value) draw(performance.now(), true);
    for (let index = 0; index < decorations.length; index++) {
      const element = decorations[index];
      if (value) element.style.opacity = '0';
      else if (element !== el('couple')) element.style.removeProperty('opacity');
      const targetOpacity = getComputedStyle(element).opacity;
      if (!reducedMotion.matches) viewAnimations.push(element.animate(
        [{opacity:decorationOpacity[index]}, {opacity:targetOpacity}],
        {duration:760, easing:'cubic-bezier(.22,1,.36,1)', fill:'both'},
      ));
    }
    if (reducedMotion.matches || !nodes.length) { finishMorph(version); return; }
    // FLIP the same buttons from their current screen positions to their new layout.
    for (const node of nodes) {
      const source = portraits.get(node.person.id);
      const target = node.button.querySelector('.avatar-wrap').getBoundingClientRect();
      const dx = source.rect.x+source.rect.width/2-target.x-target.width/2;
      const dy = source.rect.y+source.rect.height/2-target.y-target.height/2;
      const ratio = source.rect.width/target.width;
      const base = getComputedStyle(node.button).transform;
      const transform = base === 'none' ? 'matrix(1,0,0,1,0,0)' : base;
      viewAnimations.push(node.button.animate([
        {transform:`translate(${dx}px,${dy}px) ${transform} scale(${ratio})`, opacity:source.opacity},
        {transform, opacity:getComputedStyle(node.button).opacity},
      ], {duration:760, delay:(node.index%8)*14, easing:'cubic-bezier(.22,1,.36,1)', fill:'both'}));
    }
    drawMorphConnections(version);
    Promise.allSettled(viewAnimations.map(animation => animation.finished)).then(() => finishMorph(version));
  }
  function rotate(dx, dy) {
    if (listMode || morphing) return;
    if (selected) reset();
    hovered = null; targetYaw += dx; targetPitch += dy;
    interactionTime = performance.now(); wake();
  }
  function changeZoom(amount) { targetZoom = clamp(targetZoom+amount, .65, 1.7); interactionTime = performance.now(); wake(); }

  sphere.addEventListener('wheel', event => {
    event.preventDefault();
    if (event.ctrlKey) changeZoom(-event.deltaY*.005);
    else {
      const units = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? height : 1;
      rotate(0, clamp((event.deltaY+event.deltaX)*units, -200, 200)*.0035);
    }
  }, {passive:false});
  let lastScroll = window.scrollY;
  window.addEventListener('scroll', () => {
    const delta = window.scrollY-lastScroll; lastScroll = window.scrollY;
    if (visible && !listMode && !selected) rotate(0, delta*.0025);
  }, {passive:true});
  sphere.addEventListener('pointerdown', event => {
    if (listMode || morphing || event.button !== 0 || event.target.closest('#couplePhoto, a')) return;
    pointers.set(event.pointerId, {x:event.clientX, y:event.clientY});
    moved = false; dragging = true; lastPinch = 0; sphere.classList.add('dragging');
    // Keep taps on portrait buttons native; capture only after a drag begins.
    interactionTime = performance.now(); wake();
  });
  sphere.addEventListener('pointermove', event => {
    const previous = pointers.get(event.pointerId);
    if (!previous) return;
    const dx = event.clientX-previous.x, dy = event.clientY-previous.y;
    if (Math.hypot(dx,dy) > 2) { moved = true; sphere.setPointerCapture(event.pointerId); }
    pointers.set(event.pointerId, {x:event.clientX, y:event.clientY});
    if (pointers.size === 2) {
      const [a,b] = [...pointers.values()]; const distance = Math.hypot(a.x-b.x,a.y-b.y);
      if (lastPinch) changeZoom((distance-lastPinch)*.006);
      lastPinch = distance; moved = true;
    } else if (moved) rotate(dx*.006, -dy*.005);
  });
  function endPointer(event) {
    pointers.delete(event.pointerId); lastPinch = 0;
    if (sphere.hasPointerCapture(event.pointerId)) sphere.releasePointerCapture(event.pointerId);
    dragging = pointers.size > 0; sphere.classList.toggle('dragging', dragging);
    interactionTime = performance.now(); wake();
  }
  window.addEventListener('pointerup', endPointer);
  window.addEventListener('pointercancel', endPointer);
  sphere.addEventListener('keydown', event => {
    const actions = {ArrowLeft:()=>rotate(-.15,0), ArrowRight:()=>rotate(.15,0), ArrowUp:()=>rotate(0,-.12), ArrowDown:()=>rotate(0,.12), '+':()=>changeZoom(.15), '=':()=>changeZoom(.15), '-':()=>changeZoom(-.15)};
    if (actions[event.key]) { event.preventDefault(); actions[event.key](); }
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && (selected || event.target.closest('.universe'))) { reset(); sphere.focus({preventScroll:true}); } });
  el('sphereView').onclick = () => setList(false); el('listView').onclick = () => setList(true);
  el('closeFocus').onclick = () => { reset(); sphere.focus({preventScroll:true}); };
  el('openAlbum').onclick = () => { if (selected) onSelect(selected); };
  reducedMotion.addEventListener('change', () => { autoRotate = !reducedMotion.matches; if (reducedMotion.matches) finishMorph(); wake(); });

  function coupleError(message = '') {
    el('coupleStatus').textContent = message;
    el('coupleStatus').hidden = !message;
  }
  function showCouple(url) {
    const img = el('coupleImage');
    img.onload = () => { img.hidden = false; el('couplePlaceholder').hidden = true; coupleError(); };
    img.onerror = () => { img.hidden = true; el('couplePlaceholder').hidden = false; coupleError('這張照片無法顯示，請選擇 JPG、PNG 或 WebP 照片。'); };
    img.src = url;
  }
  if (window.WEDDING_CONFIG?.COUPLE_PHOTO_URL) {
    try { showCouple(new URL(window.WEDDING_CONFIG.COUPLE_PHOTO_URL, location.href).href); }
    catch { coupleError('中央合照網址無法使用，可點選照片框更換圖片。'); }
  }
  el('couplePhoto').onclick = () => el('coupleFile').click();
  el('coupleFile').onchange = () => {
    const file = el('coupleFile').files[0];
    if (!file) return;
    if (!file.type.startsWith('image/') || file.size > 25*1024*1024) { coupleError('請選擇 25 MB 以內的圖片。'); return; }
    if (photoURL) URL.revokeObjectURL(photoURL);
    photoURL = URL.createObjectURL(file); showCouple(photoURL);
    coupleError();
    el('coupleFile').value = '';
  };
  window.addEventListener('pagehide', () => { if (photoURL) URL.revokeObjectURL(photoURL); });
  document.addEventListener('visibilitychange', () => { lastTime = 0; wake(); });
  new ResizeObserver(sizeScene).observe(sphere);
  new IntersectionObserver(entries => { visible = entries[0].isIntersecting; if (visible) wake(); }, {rootMargin:'100px'}).observe(sphere);
  window.WeddingSphere = Object.freeze({render, focus, reset, onSelect(callback) { onSelect = callback; }});
  sizeScene();
})();
