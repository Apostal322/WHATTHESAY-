import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createApiClient } from "../shared/api";
import { App } from "./App";
import { chromeVideoSource } from "./videoSource";
import "./styles.css";

const api = createApiClient();
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App api={api} videoSource={chromeVideoSource} />
    </StrictMode>,
  );
}
