import { createRoot } from "react-dom/client";
import { api } from "@/core/helpers/Api";
import KeywordSearchPage from "./KeywordSearchPage";
import "@/i18n";
import "@/assets/styles/main.css";
api.defaults.baseURL = location.origin;
createRoot(document.getElementById("root")!).render(<KeywordSearchPage />);
