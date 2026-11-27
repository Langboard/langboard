import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import { cn, copyToClipboard } from "@/core/utils/ComponentUtils";
import { useState } from "react";
import { Prism, SyntaxHighlighterProps } from "react-syntax-highlighter";
import { tomorrow } from "react-syntax-highlighter/dist/cjs/styles/prism";

const SyntaxHighlighter = Prism as unknown as React.FC<SyntaxHighlighterProps>;

export interface IMarkdownCodeBlockProps {
    code: string;
    language: string;
}

function MarkdownCodeBlock({ code, language }: IMarkdownCodeBlockProps) {
    const [isCopied, setIsCopied] = useState<bool>(false);

    const copy = async () => {
        if (!navigator.clipboard || !navigator.clipboard.writeText) {
            return;
        }

        await copyToClipboard(code);
        setIsCopied(true);
        setTimeout(() => {
            setIsCopied(false);
        }, 2000);
    };

    return (
        <Flex direction="col" mt="2" w="full" rounded="md" minW="0" className="dark overflow-hidden text-left">
            <Flex items="center" justify="between" w="full" border pl="3" pr="1.5" py="1.5" className="rounded-t-md border-b-0 bg-black/50">
                <span className="text-sm font-semibold text-white">{language}</span>
                <Button variant="ghost" size="icon-sm" onClick={copy}>
                    {isCopied ? <IconComponent icon="check" size="4" /> : <IconComponent icon="copy" size="4" />}
                </Button>
            </Flex>
            <SyntaxHighlighter
                wrapLongLines
                language={language.toLowerCase()}
                style={tomorrow}
                className={cn(
                    "!mt-0 !rounded-b-md !rounded-t-none !bg-black/70 !p-3",
                    "prose h-full w-full overflow-x-hidden border text-left text-[14px] dark:prose-invert",
                    "[&_code]:!whitespace-pre-wrap [&_code]:[overflow-wrap:anywhere]"
                )}
                children={code}
            />
        </Flex>
    );
}

export default MarkdownCodeBlock;
