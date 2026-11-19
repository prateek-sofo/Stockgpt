import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, MemoryRouter, Route, Routes } from "react-router-dom";
import { STATIC } from "./lib/api";
import Layout from "./components/Layout";
import "./index.css";
import Data from "./pages/Data";
import Performance from "./pages/Performance";
import Stocks from "./pages/Stocks";
import Today from "./pages/Today";
import Longterm from "./pages/Longterm";
import Planner from "./pages/Planner";
import Ticker from "./pages/Ticker";

// a published snapshot cannot use URL paths, so it keeps the route in memory
const Router = STATIC ? MemoryRouter : BrowserRouter;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Router>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Today />} />
          <Route path="stocks" element={<Stocks />} />
          <Route path="performance" element={<Performance />} />
          <Route path="planner" element={<Planner />} />
          <Route path="longterm" element={<Longterm />} />
          <Route path="data" element={<Data />} />
          <Route path="t/:sym" element={<Ticker />} />
        </Route>
      </Routes>
    </Router>
  </StrictMode>,
);
