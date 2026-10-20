(() => {
  'use strict';
  const book = document.getElementById('invitation-book');
  if (!book || !window.ResizeObserver || !CSS.supports('position', 'sticky')) return;

  const stage = book.querySelector('.book-stage');
  const pages = [...book.querySelectorAll('[data-book-page]')];
  const navigation = document.querySelector('.book-navigation');
  const chapters = [...navigation.querySelectorAll('a')];
  const headings = pages.map(page => page.querySelector('h1, h2'));
  const anchor = book.querySelector('.paper-anchor');
  const flight = book.querySelector('.paper-flight');
  const paper = book.querySelector('.invitation-paper');
  const intro = book.querySelector('.hero-copy');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));
  let layout = {}, frame = 0, measureFrame = 0, active = -1;
  let animationFrame = 0, animationTarget = null;
  const phaseDuration = 1000;

  document.documentElement.classList.add('book-enabled');
  navigation.hidden = false;

  function render() {
    frame = 0;
    const offset = window.scrollY - layout.start;
    const zoom = clamp((offset - layout.coverRead) / layout.zoom);
    const first = clamp((offset - layout.coverRead - layout.zoom) / layout.turn);
    const second = clamp((offset - layout.secondStart) / layout.turn);
    const coverRead = clamp(offset, 0, layout.coverRead);
    const detailsRead = clamp(offset - layout.firstEnd, 0, layout.detailsRead);
    const fit = reducedMotion.matches ? 0 : zoom;
    const mix = (from, to, amount) => from + (to - from) * amount;
    const coverHeight = mix(layout.heights[0] - coverRead, layout.scene, zoom);
    const detailsHeight = layout.heights[1] - detailsRead;
    const visibleHeight = first < 1
      ? coverHeight + (detailsHeight - coverHeight) * first
      : detailsHeight + (layout.heights[2] - detailsHeight) * second;
    // Keep the longer form from peeking out below the closed, shorter sheets.
    stage.style.clipPath = `inset(-50px -50px ${Math.max(0, layout.height - visibleHeight)}px -50px)`;

    // First fit the actual invitation paper to the stage, then turn that paper.
    // The surrounding hero copy/background never rotate with the cover.
    pages[0].style.transform = `translateY(${-coverRead}px)`;
    const introOpacity = reducedMotion.matches ? 1 : 1 - clamp(zoom * 1.8);
    const focusedIntro = intro.contains(document.activeElement);
    intro.inert = introOpacity === 0;
    if (intro.inert && focusedIntro) chapters[0].focus({ preventScroll: true });
    pages[0].style.setProperty('--intro-opacity', introOpacity);
    pages[0].style.visibility = first >= (reducedMotion.matches ? 0.5 : 1) ? 'hidden' : 'visible';
    flight.style.width = `${mix(layout.paper.width, layout.width, fit)}px`;
    flight.style.height = `${mix(layout.paper.height, layout.scene, fit)}px`;
    flight.style.transform = `translate(${-layout.paper.left * fit}px, ${(coverRead - layout.paper.top) * fit}px) rotate(${-5 * (1 - fit)}deg)`;
    flight.style.setProperty('--paper-fit', fit);
    paper.style.transform = reducedMotion.matches ? 'none' : `perspective(5000px) rotateY(${-180 * first}deg)`;
    paper.style.setProperty('--turn-shade', Math.sin(first * Math.PI) * 0.19);

    pages[1].style.transform = reducedMotion.matches
      ? `translateY(${-detailsRead}px)`
      : `translateY(${-detailsRead}px) perspective(5000px) rotateY(${-180 * second}deg)`;
    pages[1].style.visibility = zoom < 1 || second >= (reducedMotion.matches ? 0.5 : 1) ? 'hidden' : 'visible';
    pages[1].style.setProperty('--turn-shade', Math.sin(second * Math.PI) * 0.19);
    pages[2].style.visibility = second > 0 ? 'visible' : 'hidden';

    const next = second >= 0.5 ? 2 : first >= 0.5 ? 1 : 0;
    if (next !== active) {
      const focusedPage = pages.findIndex(page => page.contains(document.activeElement));
      active = next;
      pages.forEach((page, index) => {
        page.inert = index !== active;
        page.setAttribute('aria-hidden', String(index !== active));
      });
      chapters.forEach((chapter, index) => {
        if (index === active) chapter.setAttribute('aria-current', 'step');
        else chapter.removeAttribute('aria-current');
      });
      // A focused turning sheet must not leave keyboard focus on its hidden back.
      if (focusedPage !== -1 && focusedPage !== active) {
        (active === 0 && intro.inert ? chapters[0] : headings[active]).focus({ preventScroll: true });
      }
    }
    book.dataset.page = String(active);
    navigation.classList.toggle('is-past-book', offset > layout.end + stage.offsetHeight - layout.scene);
  }

  function measure() {
    measureFrame = 0;
    const top = window.innerWidth <= 760 ? 16 : 24;
    const scene = Math.max(300, window.innerHeight - top - 72);
    const zoom = Math.max(320, Math.round(scene * 0.75));
    const turn = Math.max(420, Math.round(scene * 0.9));
    book.style.setProperty('--scene-height', `${scene}px`);
    const heights = pages.map(page => page.offsetHeight);
    const coverRead = Math.max(0, heights[0] - scene);
    const detailsRead = Math.max(0, heights[1] - scene);
    const firstEnd = coverRead + zoom + turn;
    const secondStart = firstEnd + detailsRead;
    const end = secondStart + turn;
    const height = Math.max(scene, ...heights);
    book.style.setProperty('--book-height', `${height}px`);
    book.style.height = `${height + end}px`;
    // offset geometry comes from a fixed-size slot, unaffected by the animation.
    const paper = { width: anchor.offsetWidth, height: anchor.offsetHeight, left: 0, top: 0 };
    for (let node = anchor; node && node !== stage; node = node.offsetParent) {
      paper.left += node.offsetLeft;
      paper.top += node.offsetTop;
    }
    layout = { scene, zoom, turn, coverRead, detailsRead, firstEnd, secondStart, end, heights, height, paper, width: stage.offsetWidth,
      start: window.scrollY + book.getBoundingClientRect().top - top };
    render();
  }

  function requestRender() { if (!frame) frame = requestAnimationFrame(render); }
  function requestMeasure() { if (!measureFrame) measureFrame = requestAnimationFrame(measure); }
  function chapterPosition(index) {
    return layout.start + [0, layout.firstEnd, layout.end][index];
  }
  function cancelAnimation() {
    cancelAnimationFrame(animationFrame);
    animationFrame = 0;
    animationTarget = null;
  }
  function goToPage(index, focus = true) {
    cancelAnimation();
    const from = window.scrollY;
    const to = chapterPosition(index);
    const finish = () => {
      animationFrame = 0;
      animationTarget = null;
      if (focus) headings[index].focus({ preventScroll: true });
    };
    const scrollTo = top => {
      // Every animation frame scrolls instantly; native smooth scrolling has
      // browser-dependent timing and cannot guarantee the two one-second phases.
      window.scrollTo({ top, behavior: 'instant' });
      cancelAnimationFrame(frame);
      render();
    };
    if (reducedMotion.matches || Math.abs(to - from) < 4) {
      scrollTo(to);
      finish();
      return;
    }
    const zoomEnd = layout.start + layout.coverRead + layout.zoom;
    const details = chapterPosition(1);
    const forward = to > from;
    const boundaries = forward ? [zoomEnd, details] : [details, zoomEnd];
    const stops = boundaries.filter(point => forward
      ? point > from && point < to
      : point < from && point > to);
    const points = [from, ...stops, to];
    const duration = (points.length - 1) * phaseDuration;
    const started = performance.now();
    animationTarget = index;

    function tick(now) {
      const elapsed = Math.max(0, now - started);
      if (elapsed >= duration) {
        scrollTo(to);
        finish();
        return;
      }
      const phase = Math.floor(elapsed / phaseDuration);
      const progress = (elapsed % phaseDuration) / phaseDuration;
      const eased = progress < 0.5
        ? 4 * progress ** 3
        : 1 - (-2 * progress + 2) ** 3 / 2;
      scrollTo(points[phase] + (points[phase + 1] - points[phase]) * eased);
      animationFrame = requestAnimationFrame(tick);
    }
    animationFrame = requestAnimationFrame(tick);
  }

  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const index = pages.findIndex(page => `#${page.id}` === link.getAttribute('href'));
    if (index === -1) return;
    event.preventDefault();
    try { history.replaceState(null, '', `#${pages[index].id}`); } catch { /* Local HTML can disallow history changes. */ }
    goToPage(index);
  });
  window.addEventListener('scroll', requestRender, { passive: true });
  // Let guests take over immediately with a wheel, swipe, scrollbar or keyboard.
  ['wheel', 'touchstart', 'pointerdown'].forEach(type => {
    window.addEventListener(type, cancelAnimation, { passive: true });
  });
  window.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.target?.closest('input, textarea, select, [contenteditable="true"]')) return;
    if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' '].includes(event.key)) cancelAnimation();
  });
  window.addEventListener('resize', () => { cancelAnimation(); requestMeasure(); }, { passive: true });
  window.addEventListener('hashchange', () => {
    const index = pages.findIndex(page => `#${page.id}` === location.hash);
    if (index !== -1) goToPage(index);
  });
  reducedMotion.addEventListener('change', () => {
    if (reducedMotion.matches && animationTarget !== null) goToPage(animationTarget);
    else requestRender();
  });

  // The final page remains in normal document flow after the two page turns.
  // Re-measure when attendance fields expand, photos load, or the textarea grows.
  const observer = new ResizeObserver(requestMeasure);
  pages.forEach(page => observer.observe(page));
  document.fonts?.ready.then(requestMeasure);
  measure();
  // Run after the browser restores scroll on load/back, which can override an
  // earlier anchor jump while the transformed sheets are still being laid out.
  window.addEventListener('pageshow', () => requestAnimationFrame(() => {
    measure();
    const initialPage = pages.findIndex(page => `#${page.id}` === location.hash);
    if (initialPage !== -1) {
      window.scrollTo({ top: chapterPosition(initialPage), behavior: 'instant' });
      render();
    }
  }));
})();
