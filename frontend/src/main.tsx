import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import "./index.css";
import Accuracy from "./pages/Accuracy";
import Backtest from "./pages/Backtest";
import Brief from "./pages/Brief";
import Data from "./pages/Data";
import Longterm from "./pages/Longterm";
import Planner from "./pages/Planner";
import Ticker from "./pages/Ticker";
import Universe from "./pages/Universe";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Brief />} />
          <Route path="accuracy" element={<Accuracy />} />
          <Route path="backtest" element={<Backtest />} />
          <Route path="planner" element={<Planner />} />
          <Route path="universe" element={<Universe />} />
          <Route path="longterm" element={<Longterm />} />
          <Route path="data" element={<Data />} />
          <Route path="t/:sym" element={<Ticker />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
