import { ProjectCard, ProjectCardRelationship } from "@/core/models";
import ModelEdgeStore from "@/core/models/ModelEdgeStore";

export interface ICardRelationshipsUpdatedRawResponse {
    card_uid: string;
    relationships: ProjectCardRelationship.Interface[];
}

const removeCardRelationshipEdges = (card: ProjectCard.TModel) => {
    const relationships = card.relationships;
    ProjectCardRelationship.Model.deleteModels(relationships.map((relationship) => relationship.uid));
    ModelEdgeStore.removeEdge(card, relationships);

    relationships.forEach((relationship) => {
        const parentCard = ProjectCard.Model.getModel(relationship.parent_card_uid);
        const childCard = ProjectCard.Model.getModel(relationship.child_card_uid);
        if (parentCard && parentCard.uid !== card.uid) {
            ModelEdgeStore.removeEdge(parentCard, relationship);
        }
        if (childCard && childCard.uid !== card.uid) {
            ModelEdgeStore.removeEdge(childCard, relationship);
        }
    });
};

const syncCardRelationships = ({ card_uid, relationships: rawRelationships }: ICardRelationshipsUpdatedRawResponse) => {
    const card = ProjectCard.Model.getModel(card_uid);
    if (card) {
        removeCardRelationshipEdges(card);
    }

    const relationships = ProjectCardRelationship.Model.fromArray(rawRelationships, true);
    relationships.forEach((relationship) => {
        const parentCard = ProjectCard.Model.getModel(relationship.parent_card_uid);
        const childCard = ProjectCard.Model.getModel(relationship.child_card_uid);

        if (parentCard) {
            ModelEdgeStore.addEdge(parentCard, relationship);
        }
        if (childCard) {
            ModelEdgeStore.addEdge(childCard, relationship);
        }
    });
};

export default syncCardRelationships;
