// Only the window values read by the existing ACS sandbox are provided here.
// Document, canvas, storage, timers and other shims remain in the source runner.
export class MinimalJSDOM {
  constructor(_html, options = {}) {
    function Navigator() {}
    function Window() {}
    function Document() {}
    Document.prototype.createElement = function createElement() {};
    Document.prototype.createElementNS = function createElementNS() {};
    const navigator = Object.assign(Object.create(Navigator.prototype), {
      appCodeName: "Mozilla",
      appName: "Netscape",
      appVersion: "5.0 (Windows)",
      userAgent: options.userAgent,
      platform: "Win32",
      product: "Gecko",
      productSub: "20030107",
      vendor: "Google Inc.",
      vendorSub: "",
      language: "zh-CN",
      languages: ["zh-CN", "zh"],
      onLine: true,
      cookieEnabled: true,
      hardwareConcurrency: 8,
      deviceMemory: 8,
      maxTouchPoints: 0,
      doNotTrack: null,
      webdriver: false,
      plugins: [],
      mimeTypes: [],
    });
    Object.defineProperty(navigator, Symbol.toStringTag, { value: "Navigator" });
    this.window = {
      navigator,
      Navigator,
      Window,
      Document,
      HTMLDocument: Document,
      close() {},
    };
    const globals = [
      "Function", "eval", "Object", "Array", "String", "Number", "Boolean",
      "Math", "Date", "JSON", "Reflect", "RegExp", "Proxy", "Promise", "Symbol",
      "Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError", "URIError",
      "Map", "Set", "WeakMap", "WeakSet", "ArrayBuffer", "DataView",
      "Uint8Array", "Uint8ClampedArray", "Int8Array", "Uint16Array", "Int16Array",
      "Uint32Array", "Int32Array", "Float32Array", "Float64Array", "Intl",
      "URL", "URLSearchParams", "TextEncoder", "TextDecoder", "Event", "EventTarget",
      "CustomEvent", "AbortController", "AbortSignal", "atob", "btoa",
    ];
    for (const name of globals) {
      if (globalThis[name] !== undefined) this.window[name] = globalThis[name];
    }
  }
}
