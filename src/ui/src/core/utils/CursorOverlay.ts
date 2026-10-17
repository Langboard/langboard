export const createMeasureMarker = () => {
    const marker = document.createElement("span");
    marker.style.display = "inline-block";
    marker.style.width = "0";
    marker.style.padding = "0";
    marker.style.margin = "0";
    marker.style.border = "0";
    marker.style.overflow = "hidden";
    marker.textContent = "\u200b";
    return marker;
};
