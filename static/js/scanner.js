/*
  Reads a QR card with the device camera and types the result into the
  search box (the input with id "checkin-search"), then asks for results.
  Nothing is checked in automatically: staff still press the button.

  Browsers with built-in barcode detection (Chrome on Android and Mac) use it.
  Others (Windows, iPhone) load a small QR reader (jsQR) the first time the
  camera button is pressed. The camera only works on https:// pages or on
  this computer's own address (localhost / 127.0.0.1).

  If the search box has data-company-desk, a company employee's card
  (a link containing /e/) is sent to the company check-in page instead.
*/
(function () {
  var start = document.getElementById("scan-start");
  var area = document.getElementById("scan-area");
  var video = document.getElementById("scan-video");
  var stop = document.getElementById("scan-stop");
  var input = document.getElementById("checkin-search");
  if (!input) return;

  function routeEmployeeCard(text) {
    var desk = input.getAttribute("data-company-desk");
    if (desk && text.indexOf("/e/") !== -1) {
      window.location.href = desk + "?q=" + encodeURIComponent(text);
      return true;
    }
    return false;
  }

  // A USB or Bluetooth scanner "types" the link into the box.
  input.addEventListener("input", function () { routeEmployeeCard(input.value); });

  if (!start || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
  start.hidden = false;

  var stream = null;
  var readFrame = null;  // function returning a Promise of the text found, or ""
  var canvas = document.createElement("canvas");
  var context = canvas.getContext("2d", { willReadFrequently: true });

  function viaJsQR() {
    var width = Math.min(video.videoWidth, 640);
    var height = Math.round(video.videoHeight * width / video.videoWidth);
    if (!width || !height) return Promise.resolve("");
    canvas.width = width;
    canvas.height = height;
    context.drawImage(video, 0, 0, width, height);
    var image = context.getImageData(0, 0, width, height);
    var code = window.jsQR(image.data, width, height, { inversionAttempts: "dontInvert" });
    return Promise.resolve(code ? code.data : "");
  }

  function loadJsQR() {
    if (window.jsQR) return Promise.resolve(viaJsQR);
    return new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = start.getAttribute("data-jsqr");
      script.onload = function () { resolve(viaJsQR); };
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }

  function chooseReader() {
    if ("BarcodeDetector" in window) {
      return BarcodeDetector.getSupportedFormats().then(function (formats) {
        if (formats.indexOf("qr_code") === -1) throw new Error("no QR");
        var detector = new BarcodeDetector({ formats: ["qr_code"] });
        return function () {
          return detector.detect(video).then(function (codes) {
            return codes.length ? codes[0].rawValue : "";
          });
        };
      }).catch(loadJsQR);
    }
    return loadJsQR();
  }

  function finish() {
    if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    stream = null;
    area.hidden = true;
  }

  function look() {
    if (!stream) return;
    readFrame().then(function (text) {
      if (text) {
        finish();
        if (routeEmployeeCard(text)) return;
        input.value = text;
        input.dispatchEvent(new Event("search"));
      } else {
        setTimeout(function () { requestAnimationFrame(look); }, 120);
      }
    }).catch(function () { requestAnimationFrame(look); });
  }

  function fail() {
    finish();
    start.textContent = start.getAttribute("data-error");
    start.disabled = true;
  }

  start.addEventListener("click", function () {
    var reader = readFrame ? Promise.resolve(readFrame) : chooseReader();
    reader.then(function (found) {
      readFrame = found;
      return navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    }).then(function (s) {
      stream = s;
      video.srcObject = s;
      area.hidden = false;
      return video.play();
    }).then(look).catch(fail);
  });
  stop.addEventListener("click", finish);
})();
