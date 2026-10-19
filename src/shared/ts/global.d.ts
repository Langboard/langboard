type bool = boolean;

declare global {
    type bool = boolean;

    declare interface ObjectConstructor {
        entries<T>(object: T): [keyof T, T[keyof T]][];
        keys<T>(object: T): (keyof T)[];
    }
}

export { bool };
