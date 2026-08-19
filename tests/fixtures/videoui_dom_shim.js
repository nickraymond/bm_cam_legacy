/* filename: videoui_dom_shim.js
   description: headless DOM + fetch shim so the gallery page's own
   JavaScript can be executed and asserted on outside a browser.

   Purpose:  the Sprint18 empty-gallery defect lived in the ORDER two
             fetches resolve in, so a source-string test cannot see it.
             This shim is just enough DOM to run render() for real and
             count the cards it produces.
   Inputs:   the <script> bodies of GET / (concatenated), appended after
             this file, then a driver that resolves the fetches.
   Assumes:  a JavaScriptCore ("jsc") or node-class engine with Promise
             and Intl. No timers are used: ordering is done with
             microtask ticks, which is deterministic.
   Not modelled: layout, events, CSS. Only what load()->render() touches.
*/

if (typeof print !== "function") {
  var print = function () { console.log.apply(console, arguments); };
}

var PENDING = {};      /* url -> resolve fn, filled by fetch()          */
var FETCHED = [];      /* urls requested, in order                      */

function El(tag) {
  this.tagName = tag || "div";
  this.id = "";
  this.className = "";
  this.textContent = "";
  this.innerHTML = "";
  this.value = "";
  this.min = "";
  this.max = "";
  this.tabIndex = 0;
  this.scrollTop = 0;
  this.style = {};
  this.dataset = {};
  this.options = [];
  this.attrs = {};
  this.children = [];
  var self = this;
  this.classList = {
    add: function (c) { self.attrs["class:" + c] = true; },
    remove: function (c) { delete self.attrs["class:" + c]; },
    contains: function (c) { return !!self.attrs["class:" + c]; },
    toggle: function (c, on) {
      if (on === undefined) on = !self.attrs["class:" + c];
      if (on) self.attrs["class:" + c] = true;
      else delete self.attrs["class:" + c];
      return on;
    }
  };
}
/* render() clears the grid with g.innerHTML="" before repainting, and it
   repaints once per fetch that lands. Without this the shim would keep
   the old children and count them twice. */
Object.defineProperty(El.prototype, "innerHTML", {
  get: function () { return this._html || ""; },
  set: function (v) { this._html = v; this.children = []; }
});
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };
El.prototype.getAttribute = function (k) { return this.attrs[k]; };
El.prototype.focus = function () {};
El.prototype.querySelector = function () { return new El("div"); };
El.prototype.querySelectorAll = function () { return []; };

/* Two media tabs, as in the page markup. render() reads dataset.media. */
var TABS = [new El("button"), new El("button")];
TABS[0].dataset.media = "videos";
TABS[1].dataset.media = "images";

var BYID = {};
var document = {
  /* Auto-vivifying: any id the page asks for exists. Cheaper and less
     brittle than parsing the real markup, and every id the page uses is
     one it also created. */
  getElementById: function (id) {
    if (!BYID[id]) { BYID[id] = new El("div"); BYID[id].id = id; }
    return BYID[id];
  },
  createElement: function (tag) { return new El(tag); },
  querySelectorAll: function (sel) {
    return sel === ".mediatabs button" ? TABS : [];
  },
  querySelector: function () { return new El("div"); },
  addEventListener: function () {}
};
var window = { scrollTo: function () {} };

function fetch(url) {
  FETCHED.push(url);
  return new Promise(function (resolve) {
    PENDING[url] = function (payload) {
      resolve({ ok: true, json: function () { return payload; } });
    };
  });
}

/* Advance n microtask turns. The page chains .then(r=>r.json()).then(...),
   so a couple of turns per fetch are needed before its render() has run. */
function tick(n) {
  var p = Promise.resolve();
  for (var i = 0; i < n; i++) p = p.then(function () {});
  return p;
}

function cards() {
  return document.getElementById("grid").children.filter(
    function (e) { return e.className === "card"; });
}

var FAILURES = [];
function check(name, cond, detail) {
  if (cond) { print("ok   - " + name); }
  else { FAILURES.push(name + (detail ? ": " + detail : "")); 
         print("FAIL - " + name + (detail ? ": " + detail : "")); }
}
function done() {
  print(FAILURES.length ? "RESULT FAIL " + FAILURES.length : "RESULT PASS");
}
