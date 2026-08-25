"use strict";

const MIME_TYPE = "audio/webm;codecs=opus";
const CHUNK_MILLISECONDS = 250;
const main = document.querySelector("main");
const wpm = document.querySelector("#wpm");
const status = document.querySelector("#status");
const startButton = document.querySelector("#start");
const stopButton = document.querySelector("#stop");

let stream = null;
let socket = null;
let recorder = null;
let sendChain = Promise.resolve();
let endingBecauseError = false;

function setState(state, message) {
  main.dataset.state = state;
  status.textContent = message;
  startButton.disabled = !["idle", "stopped", "error"].includes(state);
  stopButton.disabled = state !== "listening";
}

function checkCapabilities() {
  if (!window.isSecureContext) throw new Error("Microphone capture requires localhost or HTTPS.");
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("Microphone access is unavailable in this browser.");
  if (typeof window.MediaRecorder === "undefined") throw new Error("Audio recording is unavailable in this browser.");
  if (typeof window.WebSocket === "undefined") throw new Error("WebSockets are unavailable in this browser.");
  if (!MediaRecorder.isTypeSupported(MIME_TYPE)) throw new Error("This browser cannot record WebM/Opus audio.");
}

function releaseMicrophone() {
  if (stream) stream.getTracks().forEach((track) => track.stop());
  stream = null;
}

function closeSocket() {
  if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
  socket = null;
}

function fail(message) {
  endingBecauseError = true;
  if (recorder && recorder.state !== "inactive") recorder.stop();
  releaseMicrophone();
  closeSocket();
  setState("error", message);
}

function openSocket() {
  return new Promise((resolve, reject) => {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${location.host}/ws/live`);
    socket.binaryType = "arraybuffer";
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", () => reject(new Error("Could not connect to the backend.")), { once: true });
    socket.addEventListener("message", handleBackendMessage);
    socket.addEventListener("close", () => {
      if (!["idle", "stopped", "error"].includes(main.dataset.state)) {
        fail("The live connection closed unexpectedly.");
      }
    });
  });
}

function handleBackendMessage(event) {
  let message;
  try {
    message = JSON.parse(event.data);
  } catch {
    fail("The backend sent an invalid message.");
    return;
  }
  if (message.type === "measurement") {
    wpm.textContent = message.wpm === null ? "WPM: —" : `WPM: ${Math.round(message.wpm)}`;
  } else if (message.type === "stopped") {
    releaseMicrophone();
    closeSocket();
    setState("stopped", "Stopped cleanly after final transcription.");
  } else if (message.type === "error" && typeof message.message === "string") {
    fail(message.message);
  } else {
    fail("The backend sent an unknown message.");
  }
}

function queueAudio(blob) {
  sendChain = sendChain.then(async () => {
    if (!blob.size) return;
    if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error("The live connection is unavailable.");
    socket.send(await blob.arrayBuffer());
  });
  sendChain.catch((error) => fail(error.message));
}

async function startSession() {
  try {
    checkCapabilities();
    endingBecauseError = false;
    sendChain = Promise.resolve();
    wpm.textContent = "WPM: —";
    setState("requesting-microphone", "Requesting microphone permission…");
    stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    setState("connecting", "Connecting to live transcription…");
    await openSocket();
    recorder = new MediaRecorder(stream, { mimeType: MIME_TYPE });
    recorder.addEventListener("dataavailable", (event) => queueAudio(event.data));
    recorder.addEventListener("error", () => fail("The browser could not continue recording audio."));
    recorder.addEventListener("stop", async () => {
      if (endingBecauseError) return;
      try {
        await sendChain;
        if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error("The live connection closed before Stop completed.");
        socket.send(JSON.stringify({ type: "stop" }));
        releaseMicrophone();
      } catch (error) {
        fail(error.message);
      }
    });
    recorder.start(CHUNK_MILLISECONDS);
    setState("listening", `Listening (${recorder.mimeType || MIME_TYPE}).`);
  } catch (error) {
    const message = error.name === "NotAllowedError"
      ? "Microphone permission was denied. Allow access and try again."
      : error.name === "NotFoundError"
        ? "No microphone is available."
        : error.message;
    fail(message || "Could not start the microphone session.");
  }
}

function stopSession() {
  if (!recorder || recorder.state !== "recording") return;
  setState("stopping", "Stopping recording and waiting for final transcription…");
  recorder.stop();
}

startButton.addEventListener("click", startSession);
stopButton.addEventListener("click", stopSession);
