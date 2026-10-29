import { MinimalJSDOM } from "./browser-window.mjs";

var PAGE_URL = "https://fanyi.baidu.com/mtpe-individual/transText?query=feature&lang=en2zh";
var HOST_GLOBALS = [
  "setTimeout",
  "clearTimeout",
  "setInterval",
  "clearInterval",
  "queueMicrotask",
  "encodeURIComponent",
  "decodeURIComponent",
  "encodeURI",
  "decodeURI",
  "escape",
  "unescape",
  "Math",
  "Date",
  "JSON",
  "parseInt",
  "parseFloat",
  "isNaN",
  "isFinite",
  "Array",
  "Object",
  "String",
  "Number",
  "Boolean",
  "RegExp",
  "Error",
  "EvalError",
  "RangeError",
  "ReferenceError",
  "SyntaxError",
  "TypeError",
  "URIError",
  "Promise",
  "Symbol",
  "Map",
  "Set",
  "WeakMap",
  "WeakSet",
  "Reflect",
  "Proxy",
  "BigInt",
  "Infinity",
  "NaN",
  "undefined",
  "Uint8Array",
  "Uint8ClampedArray",
  "Uint16Array",
  "Uint32Array",
  "Int8Array",
  "Int16Array",
  "Int32Array",
  "Float32Array",
  "Float64Array",
  "DataView",
  "ArrayBuffer",
  "TextEncoder",
  "TextDecoder",
  "atob",
  "btoa"
];
function makeCanvasContext() {
  const noop5 = () => void 0;
  return new Proxy(
    {
      getImageData: () => ({ data: new Uint8ClampedArray(4), width: 0, height: 0 }),
      measureText: () => ({ width: 0 }),
      createLinearGradient: () => ({ addColorStop: noop5 }),
      createRadialGradient: () => ({ addColorStop: noop5 }),
      canvas: null
    },
    {
      get(target, prop) {
        if (prop in target) return target[prop];
        if (typeof prop === "string") return noop5;
        return void 0;
      },
      set() {
        return true;
      }
    }
  );
}
function makeElement(tagName) {
  const el = {
    tagName: String(tagName || "div").toUpperCase(),
    nodeType: 1,
    style: {},
    children: [],
    setAttribute(key, value) {
      this[key] = value;
    },
    getAttribute() {
      return null;
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    removeChild() {
    },
    remove() {
    },
    addEventListener() {
    },
    removeEventListener() {
    },
    dispatchEvent() {
      return true;
    },
    focus() {
    },
    blur() {
    },
    click() {
    },
    getBoundingClientRect() {
      return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
    },
    getContext() {
      return makeCanvasContext();
    },
    toDataURL() {
      return "data:,";
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    cloneNode() {
      return makeElement(tagName);
    }
  };
  if (String(tagName).toLowerCase() === "iframe") {
    const iframeWindow = { Object, parentWindow: { Object } };
    iframeWindow.document = {
      open() {
      },
      write(html) {
        if (String(html).includes("document.F=Object")) iframeWindow.$ = Object;
      },
      close() {
      },
      cookie: "",
      domain: ""
    };
    el.contentWindow = iframeWindow;
    el.contentDocument = iframeWindow.document;
  }
  return el;
}
function makeDocument() {
  const doc = {
    cookie: "",
    domain: "baidu.com",
    referrer: "",
    readyState: "complete",
    visibilityState: "visible",
    hidden: false,
    characterSet: "UTF-8",
    createElement: (tag) => makeElement(tag),
    createElementNS: (_ns, tag) => makeElement(tag),
    createEvent(type) {
      return {
        type,
        target: null,
        currentTarget: null,
        bubbles: false,
        cancelable: false,
        detail: null,
        timeStamp: Date.now(),
        initEvent() {
        },
        initCustomEvent() {
        },
        preventDefault() {
        },
        stopPropagation() {
        },
        stopImmediatePropagation() {
        }
      };
    },
    createTextNode(text) {
      return { nodeType: 3, textContent: text };
    },
    createDocumentFragment() {
      return makeElement("#fragment");
    },
    getElementById() {
      return null;
    },
    getElementsByTagName() {
      return [];
    },
    getElementsByClassName() {
      return [];
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    addEventListener() {
    },
    removeEventListener() {
    },
    hasFocus() {
      return true;
    },
    getSelection() {
      return { toString() {
        return "";
      } };
    },
    fonts: {
      add() {
      },
      check() {
        return true;
      },
      load() {
        return Promise.resolve([]);
      },
      ready: Promise.resolve({}),
      size: 0
    }
  };
  doc.documentElement = makeElement("html");
  doc.head = makeElement("head");
  doc.body = makeElement("body");
  return doc;
}
export function createSandbox({ userAgent }) {
  const dom = new MinimalJSDOM("<!DOCTYPE html><html><head></head><body></body></html>", {
    url: PAGE_URL,
    referrer: "https://fanyi.baidu.com/",
    userAgent,
    runScripts: "outside-only",
    pretendToBeVisual: true
  });
  const timeouts = new Set();
  const intervals = new Set();
  const sandbox = {
    setTimeout(callback, delay, ...args) {
      const handle = setTimeout(() => { timeouts.delete(handle); callback(...args); }, delay);
      timeouts.add(handle);
      return handle;
    },
    clearTimeout(handle) { clearTimeout(handle); timeouts.delete(handle); },
    setInterval(callback, delay, ...args) {
      const handle = setInterval(callback, delay, ...args);
      intervals.add(handle);
      return handle;
    },
    clearInterval(handle) { clearInterval(handle); intervals.delete(handle); },
  };
  for (const key of Object.getOwnPropertyNames(dom.window)) {
    try {
      sandbox[key] = dom.window[key];
    } catch {
    }
  }
  for (const key of HOST_GLOBALS) {
    if (!(key in sandbox)) sandbox[key] = globalThis[key];
  }
  Object.assign(sandbox, {
    window: sandbox,
    self: sandbox,
    globalThis: sandbox,
    top: sandbox,
    parent: sandbox,
    frames: sandbox,
    opener: null,
    closed: false,
    origin: "https://fanyi.baidu.com",
    console: Object.fromEntries(["log", "info", "debug", "warn", "error", "trace", "time", "timeEnd"].map(name => [name, () => {}]))
  });
  sandbox.document = makeDocument();
  sandbox.location = {
    href: PAGE_URL,
    protocol: "https:",
    host: "fanyi.baidu.com",
    hostname: "fanyi.baidu.com",
    port: "",
    pathname: "/mtpe-individual/transText",
    search: "?query=feature&lang=en2zh",
    hash: "",
    origin: "https://fanyi.baidu.com",
    reload() {
    },
    replace() {
    },
    assign() {
    }
  };
  sandbox.screen = {
    width: 1920,
    height: 1080,
    availWidth: 1920,
    availHeight: 1040,
    colorDepth: 24,
    pixelDepth: 24,
    orientation: { type: "landscape-primary", angle: 0 }
  };
  sandbox.visualViewport = { width: 1920, height: 937, scale: 1, offsetLeft: 0, offsetTop: 0 };
  sandbox.devicePixelRatio = 1;
  sandbox.innerWidth = 1920;
  sandbox.innerHeight = 937;
  sandbox.outerWidth = 1920;
  sandbox.outerHeight = 1080;
  sandbox.performance = {
    now: () => Date.now(),
    timeOrigin: Date.now(),
    timing: { navigationStart: Date.now() - 100 },
    navigation: { type: 0, redirectCount: 0 },
    getEntries: () => [],
    getEntriesByType: () => [],
    getEntriesByName: () => [],
    mark() {
    },
    measure() {
    },
    toJSON: () => ({})
  };
  const navigator = sandbox.navigator;
  navigator.connection = {
    effectiveType: "4g",
    downlink: 10,
    rtt: 50,
    saveData: false,
    type: "wifi",
    addEventListener() {
    },
    removeEventListener() {
    }
  };
  navigator.getBattery = () => Promise.resolve({ charging: true, level: 1 });
  navigator.vibrate = () => true;
  navigator.getGamepads = () => [null, null, null, null];
  navigator.sendBeacon = () => true;
  sandbox.history = {
    length: 1,
    state: null,
    back() {
    },
    forward() {
    },
    go() {
    },
    pushState() {
    },
    replaceState() {
    }
  };
  sandbox.localStorage = { getItem() {
    return null;
  }, setItem() {
  }, removeItem() {
  }, clear() {
  }, key() {
    return null;
  }, length: 0 };
  sandbox.sessionStorage = sandbox.localStorage;
  sandbox.requestAnimationFrame = (cb) => sandbox.setTimeout(() => cb(Date.now()), 16);
  sandbox.cancelAnimationFrame = sandbox.clearTimeout;
  sandbox.getComputedStyle = () => ({ getPropertyValue() {
    return "";
  } });
  sandbox.matchMedia = (query) => ({
    matches: false,
    media: query,
    addEventListener() {
    },
    removeEventListener() {
    },
    addListener() {
    },
    removeListener() {
    },
    onchange: null
  });
  sandbox.speechSynthesis = { speak() {
  }, cancel() {
  }, pause() {
  }, resume() {
  }, getVoices() {
    return [];
  } };
  sandbox.crypto = globalThis.crypto;
  sandbox.Element = function() {
  };
  sandbox.HTMLElement = function() {
  };
  sandbox.Image = function() {
    return { addEventListener() {
    }, setAttribute() {
    } };
  };
  sandbox.MutationObserver = function() {
    this.observe = () => {
    };
    this.disconnect = () => {
    };
  };
  sandbox.ResizeObserver = function() {
    this.observe = () => {
    };
    this.disconnect = () => {
    };
  };
  sandbox.fetch = async () => {
    throw new Error("network access disabled in ACS sandbox");
  };
  sandbox.XMLHttpRequest = function() {
  };
  sandbox.XMLHttpRequest.prototype.open = function() {
  };
  sandbox.XMLHttpRequest.prototype.send = function() {
  };
  return {
    sandbox,
    close() {
      for (const handle of timeouts) clearTimeout(handle);
      for (const handle of intervals) clearInterval(handle);
      timeouts.clear();
      intervals.clear();
      dom.window.close();
    },
  };
}
