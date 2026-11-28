import { Children } from "react";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";

interface ISuccessResultProps {
    title: string;
    children: React.ReactNode;
    buttons: React.JSX.Element;
}

function SuccessResult({ title, children, buttons }: ISuccessResultProps): React.JSX.Element {
    let buttonWrapperClassNames = "justify-between xs:justify-end";
    if (Children.count(buttons) === 1) {
        buttonWrapperClassNames = "justify-center";
    }

    return (
        <Flex direction="col" items="center" gap="3" mt={{ initial: "11", xs: "0" }}>
            <IconComponent icon="circle-check" size="8" className="text-green-500" />
            <h2 className="text-center text-2xl">{title}</h2>
            <h5 className="text-base">{children}</h5>
            <Flex items="center" gap="3" mt="8" className={buttonWrapperClassNames}>
                {buttons}
            </Flex>
        </Flex>
    );
}

export default SuccessResult;
