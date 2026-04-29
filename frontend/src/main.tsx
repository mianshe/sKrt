import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { installNativeStorageBridge } from "./lib/nativeStorageBridge";

async function clearLocalDevPwaState(): Promise<void> {
  if (typeof window === "undefined") return;

  try {
    if ("serviceWorker" in navigator) {
      const registrations = await navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations.map((registration) => registration.unregister()));
    }
  } catch {
    // ignore cleanup failures in local dev
  }

  try {
    if ("caches" in window) {
      const cacheKeys = await window.caches.keys();
      await Promise.all(cacheKeys.map((key) => window.caches.delete(key)));
    }
  } catch {
    // ignore cleanup failures in local dev
  }
}

void clearLocalDevPwaState();
installNativeStorageBridge();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
