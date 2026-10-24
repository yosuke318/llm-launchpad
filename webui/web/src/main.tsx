import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import i18n from "./i18n";
import "./App.css";

document.documentElement.lang = i18n.language;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
