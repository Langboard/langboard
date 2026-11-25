const projectColors = ["#4d477b", "#61405f", "#3b516f", "#47605a", "#654b40", "#425868"];

export function projectIdentityColor(projectUID: string): string {
    let hash = 0;
    for (const character of projectUID) {
        hash = (Math.imul(hash, 31) + character.charCodeAt(0)) >>> 0;
    }
    return projectColors[hash % projectColors.length];
}
