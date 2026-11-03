import { useReducedMotion } from "framer-motion";
import { useInView } from "react-intersection-observer";
import { useSyncExternalStore } from "react";

const subscribeVisibility = (notify: () => void) => {
    document.addEventListener("visibilitychange", notify);
    return () => document.removeEventListener("visibilitychange", notify);
};
const getVisibility = () => !document.hidden;

/** Existing observer pool avoids one IntersectionObserver instance per card. */
export default function useCardMotionVisibility() {
    const { ref, inView } = useInView({ initialInView: false });
    const reducedMotion = useReducedMotion() ?? true;
    const pageVisible = useSyncExternalStore(subscribeVisibility, getVisibility, () => false);
    return { ref, visible: inView && pageVisible, reducedMotion };
}
