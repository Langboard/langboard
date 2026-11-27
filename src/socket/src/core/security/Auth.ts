import User from "@/models/User";
import http from "http";
import cookie from "cookie";
import jwt from "jsonwebtoken";
import { JWT_ALGORITHM, JWT_SECRET_KEY, PROJECT_NAME, REFRESH_TOKEN_NAME } from "@/Constants";
import Encryptor from "@/core/security/Encryptor";
import { Utils } from "@langboard/core/utils";

const API_TOKEN_HEADER = "x-api-token";

class Auth {
    public static async validateToken(type: "socket", params: URLSearchParams): Promise<User | null>;
    public static async validateToken(type: "http", headers: http.IncomingHttpHeaders): Promise<User | null>;
    public static async validateToken(type: "socket" | "http", paramsOrHeaders: URLSearchParams | http.IncomingHttpHeaders): Promise<User | null> {
        let token: string | undefined = undefined;
        switch (type) {
            case "socket":
                token = (paramsOrHeaders as URLSearchParams).get("authorization") ?? undefined;
                break;
            case "http":
                {
                    const apiToken = (paramsOrHeaders as http.IncomingHttpHeaders)[API_TOKEN_HEADER];
                    if (typeof apiToken === "string") {
                        const decodedApiToken = Auth.#decodeAccessToken(apiToken, true);
                        if (!decodedApiToken?.internal) {
                            return null;
                        }

                        const user = await User.findById(decodedApiToken.sub);
                        return user;
                    }

                    token = (paramsOrHeaders as http.IncomingHttpHeaders).authorization;
                    const [bearer, accessToken] = (token ?? "").split(" ");
                    if (bearer?.toLowerCase() !== "bearer" || !accessToken) {
                        return null;
                    }

                    token = accessToken;

                    const cookies = cookie.parse((paramsOrHeaders as http.IncomingHttpHeaders).cookie ?? "");
                    const refreshToken = cookies[REFRESH_TOKEN_NAME]?.replace(/"/g, "");
                    if (!Auth.#compareTokens(token, refreshToken)) {
                        return null;
                    }
                }
                break;
        }

        if (!token) {
            return null;
        }

        const decodedAccessToken = Auth.#decodeAccessToken(token);
        if (!decodedAccessToken) {
            return null;
        }

        const user = await User.findById(decodedAccessToken.sub);
        return user;
    }

    static #compareTokens(accessToken?: string, refreshToken?: string): bool {
        if (!accessToken || !refreshToken) {
            return false;
        }

        const decodedAccessToken = this.#decodeAccessToken(accessToken);
        const decodedRefreshToken = this.#decodeRefreshToken(refreshToken);
        if (!decodedAccessToken || !decodedRefreshToken) {
            return false;
        }

        return decodedAccessToken.sub === decodedRefreshToken.sub;
    }

    static #decodeAccessToken(accessToken: string, allowInternal = false) {
        try {
            const decoded = jwt.verify(accessToken, JWT_SECRET_KEY, {
                algorithms: [JWT_ALGORITHM],
                ignoreExpiration: true,
                issuer: PROJECT_NAME,
            }) as { sub: string; exp: number; iss: string; internal?: string };

            if (
                !decoded ||
                Utils.Type.isString(decoded) ||
                decoded.iss !== PROJECT_NAME ||
                !decoded.exp ||
                new Date(decoded.exp * 1000).getTime() < new Date().getTime() ||
                (!allowInternal && decoded.internal)
            ) {
                return null;
            }

            return decoded;
        } catch {
            return null;
        }
    }

    static #decodeRefreshToken(refreshToken: string) {
        try {
            const decoded = JSON.parse(Encryptor.decrypt(refreshToken, JWT_SECRET_KEY)) as { sub: string; exp: number; iss: string };

            if (
                !decoded ||
                Utils.Type.isString(decoded) ||
                decoded.iss !== PROJECT_NAME ||
                !decoded.exp ||
                new Date(decoded.exp * 1000).getTime() < new Date().getTime()
            ) {
                return null;
            }

            return decoded;
        } catch {
            return null;
        }
    }
}

export default Auth;
