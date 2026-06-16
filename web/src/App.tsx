import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import History from "./pages/History";
import Query from "./pages/Query";
import Report from "./pages/Report";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/history" element={<History />} />
        <Route path="/query" element={<Query />} />
        <Route path="/report/:id" element={<Report />} />
      </Routes>
    </Layout>
  );
}
