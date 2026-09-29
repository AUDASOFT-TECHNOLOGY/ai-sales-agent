import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "@fontsource-variable/plus-jakarta-sans";
import "@fontsource/ibm-plex-sans-arabic/400.css";
import "@fontsource/ibm-plex-sans-arabic/500.css";
import "@fontsource/ibm-plex-sans-arabic/600.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/glass.css";
import "./styles/ui.css";
import "./styles/agent.css";
import "./styles/shell.css";
import "./styles/screens.css";
import "./styles/calm.css";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
