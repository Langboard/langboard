import BoardCardCheckitemMoreMenuCardify from "@/pages/BoardPage/components/card/checklist/BoardCardCheckitemMoreMenuCardify";
import BoardCardCheckitemMoreMenuDelete from "@/pages/BoardPage/components/card/checklist/BoardCardCheckitemMoreMenuDelete";
import BoardCardCheckitemMoreMenuDeadline from "@/pages/BoardPage/components/card/checklist/BoardCardCheckitemMoreMenuDeadline";
import BoardCardCheckitemMoreMenuEdit from "@/pages/BoardPage/components/card/checklist/BoardCardCheckitemMoreMenuEdit";
import MoreMenu from "@/components/MoreMenu";
import { ModelRegistry } from "@/core/models/ModelRegistry";

function BoardCardCheckitemMoreMenu(): React.JSX.Element {
    const { model: checkitem } = ModelRegistry.ProjectCheckitem.useContext();
    const cardifieidCards = checkitem.useForeignFieldArray("cardified_card");

    return (
        <MoreMenu.Root modal={false} triggerProps={{ className: "h-8 w-5 sm:size-8" }}>
            <BoardCardCheckitemMoreMenuEdit />
            <BoardCardCheckitemMoreMenuDeadline />
            {!cardifieidCards.length && <BoardCardCheckitemMoreMenuCardify />}
            <BoardCardCheckitemMoreMenuDelete />
        </MoreMenu.Root>
    );
}

export default BoardCardCheckitemMoreMenu;
