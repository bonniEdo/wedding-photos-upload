import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';

const source = await readFile(new URL('../../frontend/assets/invitation-book.js', import.meta.url), 'utf8');

function environment({reduced = false, hash = ''} = {}) {
  const listeners = new Map(), frames = new Map(), timers = new Map(), scrolls = [];
  let nextId = 0, observer, now = 0;
  const events = target => ({
    addEventListener(type, fn) {
      const key = `${target}:${type}`;
      listeners.set(key, [...listeners.get(key) || [], fn]);
    },
    emit(type, event = {}) { listeners.get(`${target}:${type}`)?.forEach(fn => fn(event)); },
  });
  const style = () => ({setProperty(name, value) { this[name] = value; }});
  const classList = () => ({values: new Set(), add(name) { this.values.add(name); }, toggle(name, on) { on ? this.values.add(name) : this.values.delete(name); }});
  const element = () => ({style: style(), attributes: {}, classList: classList(),
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    getAttribute(name) { return this.attributes[name]; },
  });
  const document = {...events('document'), documentElement: {classList: classList()}, activeElement: null};
  const pages = ['invitation', 'details', 'rsvp'].map((id, index) => {
    const page = {...element(), id, height: [1100, 1300, 1600][index]};
    const heading = {ownerPage: page, focus(options) {
      assert.equal(page.inert, false);
      if (this.ownerIntro) assert.equal(this.ownerIntro.inert, false, 'hidden intro headings must not receive focus');
      assert.equal(options.preventScroll, true); document.activeElement = this;
    }};
    page.querySelector = () => heading;
    page.contains = node => node?.ownerPage === page;
    Object.defineProperty(page, 'offsetHeight', {get: () => page.height});
    Object.defineProperty(page, 'inert', {
      get() { return this.inactive; },
      set(value) {
        this.inactive = value;
        // Browsers can clear focus immediately when its subtree becomes inert.
        if (value && this.contains(document.activeElement)) document.activeElement = null;
      },
    });
    return page;
  });
  const intro = {contains(node) { return node?.ownerIntro === this; }};
  Object.defineProperty(intro, 'inert', {
    get() { return this.inactive; },
    set(value) {
      this.inactive = value;
      if (value && this.contains(document.activeElement)) document.activeElement = null;
    },
  });
  pages[0].querySelector().ownerIntro = intro;
  const chapters = pages.map(page => ({...element(),
    getAttribute(name) { return name === 'href' ? `#${page.id}` : this.attributes[name]; },
    focus(options) { assert.equal(options.preventScroll, true); document.activeElement = this; },
  }));
  const navigation = {...element(), hidden: true, querySelectorAll: () => chapters};
  const book = {...element(), dataset: {}, getBoundingClientRect: () => ({top: 200 - window.scrollY})};
  const stage = {style: style(), offsetWidth: 1120};
  Object.defineProperty(stage, 'offsetHeight', {get: () => parseFloat(book.style['--book-height'])});
  Object.assign(pages[0], {offsetLeft: 0, offsetTop: 0, offsetParent: stage});
  const art = {offsetLeft: 620, offsetTop: 100, offsetParent: pages[0]};
  const anchor = {offsetWidth: 340, offsetHeight: 455, offsetLeft: 12, offsetTop: 5, offsetParent: art};
  const flight = {style: style()}, paper = {style: style()};
  const parts = {'.book-stage': stage, '.paper-anchor': anchor, '.paper-flight': flight, '.invitation-paper': paper, '.hero-copy': intro};
  book.querySelector = selector => parts[selector];
  book.querySelectorAll = () => pages;
  document.getElementById = () => book;
  document.querySelector = () => navigation;
  const motion = {...events('motion'), matches: reduced};
  const location = {hash};
  const window = {...events('window'), innerWidth: 1200, innerHeight: 900, scrollY: 0,
    scrollTo(options) { scrolls.push(options); this.scrollY = options.top; this.emit('scroll'); },
    ResizeObserver: class {
      constructor(callback) { observer = callback; }
      observe() {}
    },
  };
  const requestAnimationFrame = fn => { const id = ++nextId; frames.set(id, fn); return id; };
  const cancelAnimationFrame = id => frames.delete(id);
  function frame() {
    for (const id of [...frames.keys()]) {
      const fn = frames.get(id);
      if (fn) { frames.delete(id); fn(now); }
    }
  }
  function flush() {
    // One frame writes scroll; the following frame processes its scroll render.
    // Keep the same timestamp so a flush never advances an automated animation.
    frame(); frame();
  }
  function runTimers() {
    for (let count = 0; ; count++) {
      const due = [...timers].filter(([, timer]) => timer.due <= now);
      if (!due.length) return;
      assert(count < 20, 'timers must settle');
      for (const [id, timer] of due) {
        if (timers.delete(id)) timer.fn();
      }
    }
  }
  function advance(milliseconds) { now += milliseconds; runTimers(); flush(); runTimers(); }
  function finish() {
    const deadline = now + 10000;
    while (frames.size || timers.size) {
      assert(now < deadline, 'chapter animation and focus timers must finish');
      advance(16);
    }
  }
  function startClick(index) {
    let prevented = false;
    document.emit('click', {target: {closest: () => chapters[index]}, button: 0, preventDefault() { prevented = true; }});
    assert(prevented, 'chapter buttons should use the scroll timeline');
    flush();
  }
  runInNewContext(source, {window, document, location, CSS: {supports: () => true},
    ResizeObserver: window.ResizeObserver, matchMedia: () => motion, requestAnimationFrame, cancelAnimationFrame,
    performance: {now: () => now},
    history: {replaceState(_state, _title, nextHash) { location.hash = nextHash; }},
    setTimeout(fn, delay = 0) { const id = ++nextId; timers.set(id, {fn, due: now + delay}); return id; }, clearTimeout(id) { timers.delete(id); },
  });
  flush();
  // Native anchor restoration happens before pageshow and must not win over
  // the book's measured chapter coordinate.
  if (hash) window.scrollY = 200;
  window.emit('pageshow');
  flush();
  return {book, pages, chapters, document, motion, navigation, scrolls, location, stage, anchor, flight, paper, intro,
    get scrollY() { return window.scrollY; },
    advance, finish, startClick,
    interrupt(type, event = {}) { document.emit(type, event); window.emit(type, event); flush(); },
    scroll(top) { window.scrollY = top; window.emit('scroll'); flush(); },
    resizePage(index, height) { pages[index].height = height; observer(); flush(); },
    setMotion(value) { motion.matches = value; motion.emit('change'); flush(); },
    click(index) { startClick(index); finish(); return scrolls.at(-1); },
  };
}

function active(env, index) {
  assert.equal(env.book.dataset.page, String(index));
  env.pages.forEach((page, i) => {
    assert.equal(page.inert, i !== index, 'only the visible page accepts focus and input');
    assert.equal(page.attributes['aria-hidden'], String(i !== index));
    assert.equal(env.chapters[i].attributes['aria-current'], i === index ? 'step' : undefined);
  });
}

// Fixture: 804px of visible paper; 296px/496px of cover/details reading;
// 603px to fit the paper and 724px per turn. The book starts at document y=200
// with a 24px sticky inset and reserves 72px for the fixed chapter navigation.
const start = 176, zoomStart = 472, zoomEnd = 1075, details = 1799, rsvp = 3019;
const env = environment();
active(env, 0);
assert.equal(env.intro.inert, false);
assert.equal(env.stage.style.clipPath, 'inset(-50px -50px 500px -50px)', 'the longer hidden form must not peek below the cover');
assert.equal(env.flight.style.width, '340px');
assert.equal(env.flight.style.height, '455px');
assert.equal(env.pages[1].style.visibility, 'hidden');
assert.equal(env.pages[2].style.visibility, 'hidden');
env.scroll(start + 120);
assert.equal(env.pages[0].style.transform, 'translateY(-120px)', 'read the tall cover without rotating the hero');
env.document.activeElement = env.pages[0].querySelector();
env.scroll((zoomStart + zoomEnd) / 2);
assert.equal(env.intro.inert, false, 'partially visible intro remains accessible');
assert.equal(env.flight.style.width, '730px', 'fit enlarges the actual paper frame');
assert.equal(env.flight.style.height, '629.5px');
assert.equal(env.pages[1].style.visibility, 'hidden', 'banquet information stays hidden until the paper fits');
assert.match(env.paper.style.transform, /rotateY\(0deg\)/, 'the paper must fit before it turns');
assert(!env.pages[0].style.transform.includes('rotate'), 'hero copy and background never rotate');
env.scroll(zoomEnd);
assert.equal(env.intro.inert, true, 'fully faded intro leaves the tab order');
assert.equal(env.document.activeElement, env.chapters[0], 'focus moves from the faded intro to visible chapter navigation');
assert.equal(env.flight.style.width, '1120px');
assert.equal(env.flight.style.height, '804px');
assert.equal(env.flight.style.transform, 'translate(-632px, 191px) rotate(0deg)', 'fitted paper aligns with the stage origin');
assert.equal(env.pages[1].style.visibility, 'visible');
env.document.activeElement = {ownerPage: env.pages[0]};
env.scroll(details);
active(env, 1);
assert.equal(env.document.activeElement, env.pages[1].querySelector(), 'move focus off the old inert sheet');
assert.equal(env.pages[0].style.visibility, 'hidden');
assert.equal(env.pages[2].style.visibility, 'hidden', 'the form stays hidden until the second turn');
env.scroll(details + 200);
assert.match(env.pages[1].style.transform, /translateY\(-200px\).*rotateY\(0deg\)/, 'read all banquet information before the second turn');
active(env, 1);
env.scroll(rsvp);
active(env, 2);
assert.equal(env.stage.style.clipPath, 'inset(-50px -50px 0px -50px)', 'the complete form becomes visible after opening');
assert.equal(env.pages[1].style.visibility, 'hidden');
assert.equal(parseFloat(env.book.style.height) - env.stage.offsetHeight, rsvp - start, 'sticky runway ends at the form chapter');

env.scroll(details);
active(env, 1);
assert.match(env.pages[1].style.transform, /translateY\(0px\)/, 'reverse scrolling restores the top of the details page');
env.scroll(zoomEnd + 724 * 0.25);
active(env, 0);
assert.equal(env.intro.inert, true);
assert.equal(env.document.activeElement, env.chapters[0], 'reverse turning avoids focusing an invisible cover heading');
env.scroll(start);
active(env, 0);
assert.equal(env.intro.inert, false, 'reverse scrolling restores the intro tab order');
assert.equal(env.pages[0].style.visibility, 'visible');
assert.equal(env.flight.style.width, '340px', 'reverse scrolling returns the paper to its original slot size');
assert.equal(env.flight.style.height, '455px');
assert.equal(env.flight.style.transform, 'translate(0px, 0px) rotate(-5deg)');
assert.equal(env.pages[1].style.visibility, 'hidden');
assert.equal(env.pages[2].style.visibility, 'hidden');
assert.equal(env.click(1).top, details);
assert.equal(env.location.hash, '#details');
assert.equal(env.click(2).top, rsvp);
assert.equal(env.document.activeElement, env.pages[2].querySelector());

const originalHeight = parseFloat(env.book.style.height);
env.resizePage(2, 2200);
assert.equal(parseFloat(env.book.style.height), originalHeight + 600, 'attendance expansion adds normal document scrolling');
assert.equal(env.click(2).top, rsvp, 'a longer RSVP form must not move the page-turn endpoint');
assert.equal(parseFloat(env.book.style.height) - env.stage.offsetHeight, rsvp - start);
env.scroll(rsvp + 500);
active(env, 2);
assert.equal(env.pages[2].style.transform, undefined, 'the final form uses natural document flow');

env.scroll(zoomEnd + 724 * 0.25);
assert.match(env.paper.style.transform, /rotateY\(-45deg\)/, 'only the paper turns left after fitting, following scroll position');
assert(!env.pages[0].style.transform.includes('rotate'));
env.setMotion(true);
assert.equal(env.intro.inert, false, 'reduced motion keeps the intro visible and accessible');
env.pages.slice(0, 2).forEach(page => assert(!page.style.transform.includes('rotate'), 'reduced motion removes 3D rotation'));
assert.equal(env.paper.style.transform, 'none');
assert.equal(env.flight.style.width, '340px', 'reduced motion skips the paper enlargement');
assert.equal(env.click(2).behavior, 'instant');
active(env, 2);

const timed = environment();
timed.scroll(start);
timed.startClick(1);
timed.advance(999);
assert(parseFloat(timed.flight.style.width) < 1120, 'fit must take the full first second');
assert.match(timed.paper.style.transform, /rotateY\(0deg\)/);
timed.advance(1);
assert.equal(timed.scrollY, zoomEnd, 'the first second completes fitting');
assert.equal(timed.flight.style.width, '1120px');
assert.equal(timed.flight.style.height, '804px');
assert.match(timed.paper.style.transform, /rotateY\(0deg\)/, 'the paper has not turned at exactly 1000ms');
timed.advance(500);
assert.match(timed.paper.style.transform, /rotateY\(-90deg\)/, 'at 1500ms the paper is halfway through the one-second left turn');
timed.advance(500);
assert.equal(timed.scrollY, details, 'at 2000ms the banquet page is fully open');
active(timed, 1);
timed.startClick(2);
timed.advance(1000);
assert.equal(timed.scrollY, rsvp, 'the second button opens the form in one second');
active(timed, 2);
assert(timed.scrolls.every(call => call.behavior === 'instant'), 'rAF owns timing; native smooth scrolling must not add another animation');
timed.startClick(1);
timed.advance(1000);
assert.equal(timed.scrollY, details, 'the reverse chapter button closes the form turn');
active(timed, 1);
timed.click(0);
assert.equal(timed.scrollY, start, 'the cover button reverses the fit and turn route');
active(timed, 0);
assert.equal(timed.flight.style.width, '340px');

for (const [type, event] of [['wheel', {}], ['touchstart', {}], ['pointerdown', {}], ['keydown', {key: 'ArrowDown'}], ['resize', {}]]) {
  const interrupted = environment();
  interrupted.scroll(start);
  interrupted.startClick(1);
  interrupted.advance(500);
  const position = interrupted.scrollY;
  interrupted.interrupt(type, event);
  interrupted.advance(2500);
  assert.equal(interrupted.scrollY, position, `${type} must cancel automatic scrolling`);
  interrupted.scroll(start + 120);
  assert.equal(interrupted.pages[0].style.transform, 'translateY(-120px)', 'manual scrolling still directly controls the page');
}
const replaced = environment();
replaced.scroll(start);
replaced.startClick(1);
replaced.advance(500);
replaced.click(0);
assert.equal(replaced.scrollY, start, 'a new chapter click replaces the unfinished invitation animation');
replaced.advance(3000);
assert.equal(replaced.scrollY, start, 'the cancelled animation cannot later reopen the banquet page');

const immediate = environment({reduced: true});
immediate.startClick(1);
assert.equal(immediate.scrollY, details, 'reduced motion jumps to the requested chapter without waiting');
assert.equal(immediate.paper.style.transform, 'none');
immediate.advance(2000);
assert.equal(immediate.scrollY, details);

const motionChanged = environment();
motionChanged.startClick(2);
motionChanged.advance(500);
motionChanged.setMotion(true);
assert.equal(motionChanged.scrollY, rsvp, 'enabling reduced motion finishes the current destination immediately');
active(motionChanged, 2);
motionChanged.advance(3000);
assert.equal(motionChanged.scrollY, rsvp, 'the cancelled timed animation cannot resume after reduced motion changes');

const direct = environment({hash: '#rsvp', reduced: true});
assert.equal(direct.scrolls.at(-1).top, rsvp, 'a direct RSVP link opens the form rather than the cover');
assert.equal(direct.scrolls.at(-1).behavior, 'instant');
active(direct, 2);
console.log('PASS: paper-only fit then left flip, exact one-second button stages, reverse/replaced/interrupted animations, hidden inner pages, tall-page reading, focus/inert state, growing RSVP form, reduced motion and initial RSVP hash.');
