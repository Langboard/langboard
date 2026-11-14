import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import Skeleton from "@/components/base/Skeleton";
import { Utils } from "@langboard/core/utils";

export function SkeletonCardsPage(): React.JSX.Element {
    return (
        <>
            {Array.from({ length: 4 }).map((_, index) => (
                <Flex items="center" w="full" key={Utils.String.Token.shortUUID()} mt={index ? "2" : "0"}>
                    <Box px="4" className="w-1/3">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/3">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/6">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/6">
                        <Skeleton h="10" w="full" />
                    </Box>
                </Flex>
            ))}
        </>
    );
}

export function SkeletonTrackingPage(): React.JSX.Element {
    return (
        <>
            {Array.from({ length: 4 }).map((_, index) => (
                <Flex items="center" w="full" key={Utils.String.Token.shortUUID()} mt={index ? "2" : "0"}>
                    <Box px="4" className="w-1/4">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/4">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/6">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/6">
                        <Skeleton h="10" w="full" />
                    </Box>
                    <Box px="4" className="w-1/6">
                        <Skeleton h="10" w="full" />
                    </Box>
                </Flex>
            ))}
        </>
    );
}
