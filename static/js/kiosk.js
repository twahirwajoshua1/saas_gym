/*
  The entrance tablet: keeps the camera on, reads QR cards, and shows the
  answer (welcome, choose services, or "see reception") over the screen.
  While an answer is showing, scanning pauses; it closes by itself after a
  few seconds (or with Cancel), and the next person can scan.
*/
(function () {
  var root = document.getElementById("kiosk");
  var overlay = document.getElementById("kiosk-overlay");
  var video = document.getElementById("kiosk-video");
  var hint = document.getElementById("kiosk-camera-hint");
  var clock = document.getElementById("kiosk-clock");
  if (!root || !overlay) return;

  setInterval(function () {
    var now = new Date();
    clock.textContent = String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
  }, 15000);

  var closeTimer = null;
  function close() { overlay.innerHTML = ""; }
  overlay.addEventListener("click", function (event) {
    if (event.target.closest("[data-close]")) close();
  });
  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (event.detail.target !== overlay) return;
    clearTimeout(closeTimer);
    var layer = overlay.querySelector("[data-close-after]");
    if (layer) closeTimer = setTimeout(close, parseInt(layer.getAttribute("data-close-after"), 10));
  });

  var lastText = "", lastAt = 0;
  function found(text) {
    var now = Date.now();
    if (text === lastText && now - lastAt < 5000) return;  // the same card held up twice
    lastText = text; lastAt = now;
    htmx.ajax("POST", root.getAttribute("data-scan-url"), { target: overlay, values: { text: text } });
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
  var canvas = document.createElement("canvas");
  var context = canvas.getContext("2d", { willReadFrequently: true });
  var readFrame = null;

  function viaJsQR() {
    var width = Math.min(video.videoWidth, 640);
    var height = Math.round(video.videoHeight * width / video.videoWidth);
    if (!width || !height) return Promise.resolve("");
    canvas.width = width; canvas.height = height;
    context.drawImage(video, 0, 0, width, height);
    var code = window.jsQR(context.getImageData(0, 0, width, height).data, width, height, { inversionAttempts: "dontInvert" });
    return Promise.resolve(code ? code.data : "");
  }
  function loadJsQR() {
    if (window.jsQR) return Promise.resolve(viaJsQR);
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = root.getAttribute("data-jsqr");
      s.onload = function () { resolve(viaJsQR); };
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }
  function chooseReader() {
    if ("BarcodeDetector" in window) {
      return BarcodeDetector.getSupportedFormats().then(function (formats) {
        if (formats.indexOf("qr_code") === -1) throw new Error("no QR");
        var detector = new BarcodeDetector({ formats: ["qr_code"] });
        return function () { return detector.detect(video).then(function (c) { return c.length ? c[0].rawValue : ""; }); };
      }).catch(loadJsQR);
    }
    return loadJsQR();
  }
  function look() {
    if (overlay.innerHTML.trim() !== "") { setTimeout(look, 300); return; }
    readFrame().then(function (text) {
      if (text) found(text);
      setTimeout(look, 150);
    }).catch(function () { setTimeout(look, 300); });
  }
  chooseReader().then(function (reader) {
    readFrame = reader;
    // The tablet's front camera faces the person standing in front of it.
    return navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" } });
  }).then(function (stream) {
    video.srcObject = stream;
    if (hint) hint.hidden = true;
    return video.play();
  }).then(look).catch(function () {
    if (hint) hint.lastElementChild.textContent = "Camera not available. Ask reception.";
  });
})();
