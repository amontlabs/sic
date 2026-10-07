import { Route, Routes } from "react-router";
import { Home } from "./pages/Home";
import { Live } from "./pages/Live";
import { NotFound } from "./pages/NotFound";
import { Replay } from "./pages/Replay";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/d/:id" element={<Replay />} />
      <Route path="/live/:id" element={<Live />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
