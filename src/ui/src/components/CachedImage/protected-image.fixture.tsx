import { useCallback, useState } from "react";
import { createRoot } from "react-dom/client";
import CachedImage from "./index";
function Fixture() {
    const [src, setSrc] = useState("/file/encrypted/card_attachment/one.png");
    const [visible, setVisible] = useState(false);
    const [labelUpdated, setLabelUpdated] = useState(false);
    const [updated, setUpdated] = useState(false);
    const [styled, setStyled] = useState(false);
    const [clicked, setClicked] = useState("");
    const imageRef = useCallback(
        (image: HTMLImageElement | null) => {
            if (image) image.dataset.refVersion = updated ? "updated" : "initial";
        },
        [updated]
    );
    return (
        <>
            <button onClick={() => setVisible(true)}>Show</button>
            <button onClick={() => setVisible(false)}>Hide</button>
            <button onClick={() => setSrc("/file/encrypted/card_attachment/two.png")}>Next</button>
            <button onClick={() => setSrc("https://external.example.invalid/proof.png")}>External</button>
            <button onClick={() => setLabelUpdated(true)}>Update label</button>
            <button onClick={() => setUpdated(true)}>Update props</button>
            <button onClick={() => setStyled(true)}>Update classes</button>
            <output>{clicked}</output>
            {visible && (
                <CachedImage
                    src={src}
                    alt={labelUpdated ? "ロゴ" : "attachment"}
                    title={updated ? "Updated title" : "Initial title"}
                    aria-description={updated ? "Updated description" : "Initial description"}
                    data-version={updated ? "updated" : "initial"}
                    style={{ opacity: updated ? 0.5 : 1 }}
                    onClick={() => setClicked(updated ? "Updated handler" : "Initial handler")}
                    ref={imageRef}
                    className={styled ? "updated-image" : "initial-image"}
                    w={styled ? "10" : "9"}
                    srcSet="/file/encrypted/card_attachment/bypass.png 2x"
                />
            )}
        </>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
