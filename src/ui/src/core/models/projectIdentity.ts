export const projectIdentityColors = {
    plum: "#4d477b",
    mauve: "#61405f",
    denim: "#3b516f",
    sage: "#47605a",
    clay: "#654b40",
    slate: "#425868",
};

export const projectIdentityStyles = ["auto", "logo", "icon", "abbreviation"] as const;
export const projectIdentityIcons = ["folder-kanban", "briefcase", "code", "globe", "layers"] as const;
const defaultColors = Object.values(projectIdentityColors);

export interface ProjectIdentity {
    style: (typeof projectIdentityStyles)[number];
    color?: keyof typeof projectIdentityColors | null;
    icon?: (typeof projectIdentityIcons)[number] | null;
    abbreviation?: string | null;
}

export function projectIdentityColor(projectUID: string, color?: ProjectIdentity["color"]): string {
    if (color && Object.hasOwn(projectIdentityColors, color)) {
        return projectIdentityColors[color];
    }
    let hash = 0;
    for (const character of projectUID) {
        hash = (Math.imul(hash, 31) + character.charCodeAt(0)) >>> 0;
    }
    return defaultColors[hash % defaultColors.length];
}
