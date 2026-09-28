import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { LabJarvisClient } from "./api/client";
import "./styles.css";
import "./styles/recovery.css";
import "./styles/review.css";


const root = document.getElementById("root");
if (root === null) throw new Error("LabJarvis root element is missing");

createRoot(root).render(
  <StrictMode>
    <App client={new LabJarvisClient()} />
  </StrictMode>,
);
