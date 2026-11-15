(function () {
  // ---- live filter (people, places, photos) ----
  var q = document.getElementById('q');
  if (q) {
    var items = [].slice.call(document.querySelectorAll('[data-s]'));
    var groups = [].slice.call(document.querySelectorAll('.group'));
    var none = document.getElementById('none');
    var apply = function () {
      var terms = q.value.toLowerCase().split(/\s+/).filter(Boolean), shown = 0;
      items.forEach(function (el) {
        var hay = el.dataset.s, ok = terms.every(function (t) { return hay.indexOf(t) > -1; });
        el.hidden = !ok; if (ok) shown++;
        if (el.tagName === 'DETAILS') el.open = terms.length > 0 && ok;
      });
      groups.forEach(function (g) {
        g.hidden = !g.querySelector('[data-s]:not([hidden])');
        if (g.tagName === 'DETAILS') g.open = terms.length > 0 && !g.hidden;
      });
      if (none) none.hidden = shown > 0;
      [].forEach.call(document.querySelectorAll('.az a[data-l]'), function (a) {
        var sec = document.getElementById('L-' + a.dataset.l), has = false;
        if (sec && sec.classList.contains('la')) {          // people page: any visible person before the next letter?
          for (var n = sec.nextElementSibling; n && !n.classList.contains('la'); n = n.nextElementSibling)
            if (!n.hidden) { has = true; break; }
        } else has = !!sec && !sec.hidden;
        a.classList.toggle('off', !has);
      });
    };
    var pre = new URLSearchParams(location.search).get('q');
    if (pre) q.value = pre;
    q.addEventListener('input', apply);
    apply();
  }

  // ---- places page: expand / collapse all (one button, toggles) ----
  (function () {
    var btn = document.getElementById('pexpand');
    if (!btn) return;
    var open = false;
    btn.addEventListener('click', function () {
      open = !open;
      [].forEach.call(document.querySelectorAll('details.group'), function (d) { d.open = open; });
      btn.textContent = open ? 'Collapse all' : 'Expand all';
    });
  })();

  // ---- places page: "Sort by most people" toggle (re-orders groups/places; original order restored on a second click) ----
  (function () {
    var btn = document.getElementById('psort');
    var main = document.querySelector('main');
    if (!btn || !main) return;
    var SEL = '.group, .pl', orig = new Map();
    function record(el) {
      var kids = [].slice.call(el.children).filter(function (c) { return c.matches(SEL); });
      if (kids.length) orig.set(el, kids);
      kids.forEach(record);
    }
    record(main);
    function countOf(el) {
      var y = el.querySelector(':scope > summary .yrs') || el.querySelector(':scope > .yrs');
      return y ? parseInt(y.textContent, 10) || 0 : 0;
    }
    function sortTree(el) {
      var kids = [].slice.call(el.children).filter(function (c) { return c.matches(SEL); });
      kids.sort(function (a, b) { return countOf(b) - countOf(a); });
      kids.forEach(function (c) { el.appendChild(c); });
      kids.forEach(sortTree);
    }
    function restoreTree(el) {
      var kids = orig.get(el);
      if (!kids) return;
      kids.forEach(function (c) { el.appendChild(c); restoreTree(c); });
    }
    var sorted = false;
    btn.addEventListener('click', function () {
      sorted = !sorted;
      if (sorted) sortTree(main); else restoreTree(main);
      btn.textContent = sorted ? 'Sort alphabetically' : 'Sort by most people';
    });
  })();

  // ---- new-tab links ----
  // Photos and outside web links always open in a new tab. With
  // "new_tab_links": "all" in site.json, every link does.
  var all = document.body.dataset.newtab === 'all';
  [].forEach.call(document.querySelectorAll('a[href]'), function (a) {
    var external = a.host && a.host !== location.host && /^https?:/.test(a.href);
    var photo = /\/(media\/web|photo)\//.test(a.getAttribute('href'));
    if (all || external || photo) { a.target = '_blank'; a.rel = 'noopener'; }
  });

  // ---- tagged photo page: highlight a face from the name list, show all names ----
  [].forEach.call(document.querySelectorAll('.tphoto'), function (box) {
    var marks = box.querySelectorAll('.tg');
    [].forEach.call(document.querySelectorAll('.tlist a[data-t]'), function (a) {
      var m = marks[+a.dataset.t];
      if (!m) return;
      var on = function (f) { return function () { m.classList.toggle('on', f); }; };
      a.addEventListener('mouseenter', on(true)); a.addEventListener('mouseleave', on(false));
      a.addEventListener('focus', on(true)); a.addEventListener('blur', on(false));
    });
    var btn = document.getElementById('tnames');
    if (btn) btn.addEventListener('click', function () {
      btn.textContent = box.classList.toggle('names') ? 'Hide names' : 'Show all names';
    });
  });

  // ---- chart page: descendants on the left, ancestors on the right, an arrow between each person ----
  (function () {
    var box = document.getElementById('pd');
    if (!box) return;
    var D = JSON.parse(document.getElementById('pddata').textContent), P = D.p;
    var q = document.getElementById('pdq'), gsel = document.getElementById('pdg'), wsel = document.getElementById('pdw'),
        note = document.getElementById('pdnote'), label = {}, built = 0, LIMIT = 5000, hitLimit = false;
    P.forEach(function (r, i) { label[r.n + (r.y ? ' (' + r.y + ')' : '')] = i; });
    var el = function (tag, cls) { var e = document.createElement(tag); if (cls) e.className = cls; return e; };

    function mini(i, note) {
      var r = P[i], d = el('div', 'pc fc' + (r.x === 'M' ? ' m' : r.x === 'F' ? ' f' : ''));
      var av;
      if (r.t && r.c) {
        av = el('span', 'av xs avwrap');
        var im = el('img'); im.loading = 'lazy'; im.alt = ''; im.src = 'media/thumb/' + r.t;
        im.style.objectPosition = r.c[0] + '% ' + r.c[1] + '%';
        im.style.transform = 'scale(' + r.c[2] + ')';
        im.style.transformOrigin = r.c[0] + '% ' + r.c[1] + '%';
        av.appendChild(im);
      }
      else if (r.t) { av = el('img', 'av xs'); av.loading = 'lazy'; av.alt = ''; av.src = 'media/thumb/' + r.t; }
      else { av = el('span', 'av ph xs'); av.textContent = r.n.split(/\s+/).slice(0, 2).map(function (w) { return w[0]; }).join('').toUpperCase() || '?'; }
      var t = el('div'), a = el('a');
      a.href = 'people/' + r.s + '.html'; a.textContent = r.n; t.appendChild(a);
      var yrs = (r.y || '') + (note ? (r.y ? ' \u00b7 ' : '') + note : '');
      if (yrs) { var y = el('span', 'yrs'); y.textContent = yrs; t.appendChild(y); }
      d.appendChild(av); d.appendChild(t);
      return d;
    }
    // a person's tile. With opts.spouse, a small arrow above it shows their spouses; with opts.sibs another below shows siblings.
    function card(i, opts) {
      opts = opts || {};
      var c = mini(i, ''), r = P[i], hasSb = opts.sibs && r.sb && r.sb.length, hasSp = opts.spouse && r.sp && r.sp.length;
      if (!hasSb && !hasSp) return c;
      var wrap = el('div', 'pd-cb'), sets = [];
      function side(list, panelCls, btnCls, what, up) {
        var panel = el('div', panelCls), btn = el('button', 'pd-sb ' + btnCls);
        btn.type = 'button';
        list.forEach(function (s) { panel.appendChild(s); });
        function set(open) {
          panel.classList.toggle('show', open);
          btn.setAttribute('aria-expanded', open ? 'true' : 'false');
          btn.textContent = (up === open) ? '\u02C5' : '\u02C4';
          btn.title = (open ? 'Hide ' : 'Show ') + what + ' of ' + r.n;
        }
        btn.addEventListener('click', function () { set(!panel.classList.contains('show')); });
        set(false); sets.push(set);
        c.appendChild(btn); wrap.appendChild(panel);
      }
      if (hasSp) side(r.sp.map(function (s) { return mini(s[0], s[1] ? 'm. ' + s[1] : ''); }),
        'pd-sps', 'pd-up', r.sp.length === 1 ? 'spouse' : r.sp.length + ' spouses', true);
      if (hasSb) side(r.sb.map(function (s) { return mini(s[0], s[1] ? 'half-sibling' : ''); }),
        'pd-sibs', 'pd-dn', r.sb.length === 1 ? 'sibling' : r.sb.length + ' siblings', false);
      wrap._set = function (open) { sets.forEach(function (f) { f(open); }); };
      wrap.appendChild(c); wrap._card = c;
      return wrap;
    }
    // dir 'a' = parents (to the right), 'd' = children (to the left); card=false builds only the arrow and the column
    function node(i, depth, gens, dir, withCard) {
      built++;
      var r = P[i], n = el('div', 'pd-node' + (dir === 'd' ? ' rev' : '') + (withCard ? '' : ' half'));
      var rel = dir === 'a' ? [r.f, r.m].filter(function (k) { return k >= 0; }) : r.k;
      if (withCard) n.appendChild(card(i, { spouse: dir !== 'a', sibs: false }));
      if (!rel.length) return withCard ? n : null;
      var link = el('div', 'pd-link'), btn = el('button', 'pd-tg'), col = el('div', 'pd-col'), filled = false;
      btn.type = 'button'; link.appendChild(btn); n.appendChild(link); n.appendChild(col);
      function fill() {
        if (filled) return; filled = true;
        rel.forEach(function (k) {
          var it = el('div', 'pd-item');
          it.appendChild(node(k, depth + 1, gens, dir, true)); col.appendChild(it);
        });
      }
      function set(open) {
        if (open) {
          if (!filled && built >= LIMIT) { hitLimit = true; open = false; }
          else fill();
        }
        n.classList.toggle('closed', !open);
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        var toward = dir === 'a' ? '\u2039' : '\u203A', away = dir === 'a' ? '\u203A' : '\u2039';
        btn.textContent = open ? toward : away;
        btn.title = (open ? 'Hide ' : 'Show ') + (dir === 'a' ? 'parents' : 'children') + ' of ' + r.n;
      }
      n._set = set;
      btn.addEventListener('click', function () { set(n.classList.contains('closed')); });
      set(depth + 1 < gens);
      return n;
    }
    function allDeep(open) {
      if (open) {
        var more = true;
        while (more) {
          more = false;
          [].forEach.call(box.querySelectorAll('.pd-node.closed'), function (n) { if (n._set && !hitLimit) { n._set(true); more = true; } });
        }
      } else {
        [].forEach.call(box.querySelectorAll('.pd-node'), function (n) { if (n._set) n._set(false); });
      }
      [].forEach.call(box.querySelectorAll('.pd-cb'), function (w) { w._set(open); });
      note.hidden = !hitLimit;
      note.textContent = hitLimit ? 'Stopped at ' + LIMIT.toLocaleString() + ' people. Use the arrows to open individual branches.' : '';
    }
    function show(i) {
      built = 0; hitLimit = false; box.innerHTML = ''; note.hidden = true;
      var gens = parseInt(gsel.value, 10) || 4, w = wsel.value, root = el('div', 'pd-root');
      var desc = w !== 'anc' ? node(i, 0, gens, 'd', false) : null;
      var anc = w !== 'desc' ? node(i, 0, gens, 'a', false) : null;
      if (desc) root.appendChild(desc);
      var c = card(i, { spouse: true, sibs: true }); (c._card || c).classList.add('me'); root.appendChild(c);
      if (anc) root.appendChild(anc);
      box.appendChild(root);
      document.getElementById('pdname').textContent = P[i].n;
      q.value = P[i].n + (P[i].y ? ' (' + P[i].y + ')' : '');
      try { history.replaceState(null, '', '#' + P[i].s); } catch (e) {}
      var a = c.getBoundingClientRect(), b = box.getBoundingClientRect();
      box.scrollLeft += (a.left + a.width / 2) - (b.left + b.width / 2);
    }
    function cur() {
      var h = decodeURIComponent(location.hash.slice(1));
      for (var i = 0; i < P.length; i++) if (P[i].s === h) return i;
      return D.start;
    }
    q.addEventListener('focus', function () { q.select(); });
    q.addEventListener('input', function () { if (label[q.value] !== undefined) show(label[q.value]); });
    gsel.addEventListener('change', function () { show(cur()); });
    wsel.addEventListener('change', function () { show(cur()); });
    document.getElementById('pdx').addEventListener('click', function () { allDeep(true); });
    document.getElementById('pdc').addEventListener('click', function () { allDeep(false); });
    show(cur());
  })();

  // ---- family tree: start scrolled so the person is in view ----
  [].forEach.call(document.querySelectorAll('.ft'), function (ft) {
    var me = ft.querySelector('.fc.me');
    if (!me || ft.scrollWidth <= ft.clientWidth) return;
    var a = (me.closest('.cg') || me).getBoundingClientRect(), b = ft.getBoundingClientRect();
    ft.scrollLeft += (a.left + a.width / 2) - (b.left + b.width / 2);
  });

  // ---- contact link: build the mailto: address only when clicked ----
  [].forEach.call(document.querySelectorAll('a.contact[data-m]'), function (a) {
    a.addEventListener('click', function () {
      try { a.href = 'mailto:' + atob(a.dataset.m); } catch (e) {}
    });
  });
})();
