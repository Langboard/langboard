import AvatarGeneration from "@/components/AvatarGeneration";
import AutoComplete from "@/components/base/AutoComplete";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Collaborative from "@/components/Collaborative";
import Flex from "@/components/base/Flex";
import Form from "@/components/base/Form";
import Input from "@/components/base/Input";
import Label from "@/components/base/Label";
import SubmitButton from "@/components/base/SubmitButton";
import Textarea from "@/components/base/Textarea";
import FormErrorMessage from "@/components/FormErrorMessage";
import { useCollaborativeText } from "@/components/Collaborative/useCollaborativeText";
import useChangeProjectDetails from "@/controllers/api/board/settings/useChangeProjectDetails";
import useForm from "@/core/hooks/form/useForm";
import { Project } from "@/core/models";
import {
    projectIdentityColor,
    projectIdentityColors,
    projectIdentityIcons,
    projectIdentityStyles,
    type ProjectIdentity,
} from "@/core/models/projectIdentity";
import IconComponent from "@/components/base/IconComponent";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { memo, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const BoardSettingsBasic = memo(() => {
    const { canEditBasicInfo, isBasicInfoEditing, project, setIsBasicInfoEditing } = useBoardSettings();
    const [t] = useTranslation();
    const { mutate } = useChangeProjectDetails(project.uid);
    const avatar = project.useField("avatar");
    const avatarRevision = project.useField("avatar_revision") ?? 0;
    const identity = project.useField("identity");
    const [identityDraft, setIdentityDraft] = useState<ProjectIdentity>();
    const displayIdentity = identityDraft ?? identity;
    const identityStyle = displayIdentity?.style ?? "auto";
    const changeIdentity = (values: Partial<ProjectIdentity>) =>
        setIdentityDraft((previous) => ({ style: "auto", ...(previous ?? identity), ...values }));
    const title = project.useField("title");
    const description = project.useField("description");
    const aiDescription = project.useField("ai_description");
    const projectType = project.useField("project_type");
    const archiveVisibleDays = project.useField("archive_visible_days");
    const projectTypeRef = useRef(projectType);
    const projectTypeInputRef = useRef<HTMLInputElement>(null);
    const { errors, isValidating, handleSubmit, formRef } = useForm({
        errorLangPrefix: "project.errors",
        schema: {
            title: { required: true },
            description: {},
            project_type: { required: true },
            archive_visible_days: {
                required: true,
                custom: {
                    errorKey: "invalid_archive_visible_days",
                    validate: (value) => Utils.Type.isString(value) && Number.isInteger(Number(value)) && Number(value) >= 1,
                },
            },
        },
        predefineValues: () => (identityDraft ? { identity: { ...identityDraft, abbreviation: identityDraft.abbreviation?.trim() || null } } : {}),
        mutate,
        mutateOnSuccess: (response) => {
            if (response && "identity" in response) {
                project.identity = response.identity;
            }
            setIdentityDraft(undefined);
            setIsBasicInfoEditing(false);
        },
        useDefaultBadRequestHandler: true,
    });
    const {
        remoteCursors: projectTypeRemoteCursors,
        updateSelection: updateProjectTypeSelection,
        value: collaborativeProjectType,
        updateValue: updateCollaborativeProjectType,
    } = useCollaborativeText({
        collaborationType: EEditorCollaborationType.BoardSettings,
        uid: project.uid,
        field: "project_type",
        defaultValue: projectType,
        disabled: !isBasicInfoEditing,
    });

    const setProjectType = (value: string) => {
        projectTypeRef.current = value;
        projectTypeInputRef.current!.value = value;
        updateCollaborativeProjectType(value);
    };

    useEffect(() => {
        projectTypeRef.current = collaborativeProjectType;
        if (projectTypeInputRef.current) {
            projectTypeInputRef.current.value = collaborativeProjectType;
        }
    }, [collaborativeProjectType]);

    const handleCancelEditing = useCallback(() => {
        setIdentityDraft(undefined);
        setIsBasicInfoEditing(false);
    }, [setIsBasicInfoEditing]);

    const handleStartEditing = useCallback(() => {
        if (!canEditBasicInfo) {
            return;
        }

        setIsBasicInfoEditing(true);
    }, [canEditBasicInfo, setIsBasicInfoEditing]);

    return (
        <Flex direction="col" py="4" gap="4" items="center">
            {avatar && <img src={avatar} width={96} height={96} alt={t("internalBot.avatar.current")} className="rounded-xl border object-cover" />}
            <AvatarGeneration
                base={`/board/${encodeURIComponent(project.uid)}/avatar`}
                revisionKey={`${avatar}:${avatarRevision}`}
                canUpdate={canEditBasicInfo}
                disabled={isValidating}
                onPersisted={(snapshot) => {
                    project.avatar = snapshot.avatar ?? undefined;
                    project.avatar_revision = snapshot.avatar_revision;
                }}
            />
            <Form.Root className="flex w-full max-w-96 flex-col gap-3" onSubmit={handleSubmit} ref={formRef}>
                {canEditBasicInfo && (
                    <Flex items="center" justify="end" gap="2">
                        {!isBasicInfoEditing ? (
                            <Button type="button" variant="default" onClick={handleStartEditing}>
                                {t("common.Edit")}
                            </Button>
                        ) : (
                            <>
                                <Button type="button" variant="secondary" onClick={handleCancelEditing}>
                                    {t("common.Cancel")}
                                </Button>
                                <SubmitButton type="submit" isValidating={isValidating}>
                                    {t("common.Save")}
                                </SubmitButton>
                            </>
                        )}
                    </Flex>
                )}
                <fieldset disabled={!canEditBasicInfo || !isBasicInfoEditing || isValidating} className="space-y-2">
                    <legend className="mb-2 text-sm font-medium">{t("project.identity.Project identity")}</legend>
                    <label className="grid gap-1 text-sm">
                        {t("project.identity.Display style")}
                        <select
                            className="h-9 rounded-md border bg-background px-2"
                            value={identityStyle}
                            onChange={(event) => changeIdentity({ style: event.currentTarget.value as ProjectIdentity["style"] })}
                        >
                            {projectIdentityStyles.map((style) => (
                                <option key={style} value={style}>
                                    {t(`project.identity.${style}`)}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="grid gap-1 text-sm">
                        <span className="flex items-center gap-2">
                            {t("project.identity.Project color")}
                            <span
                                aria-hidden="true"
                                className="size-3 rounded-full"
                                style={{ backgroundColor: projectIdentityColor(project.uid, displayIdentity?.color) }}
                            />
                        </span>
                        <select
                            className="h-9 rounded-md border bg-background px-2"
                            value={displayIdentity?.color ?? ""}
                            onChange={(event) => changeIdentity({ color: (event.currentTarget.value || null) as ProjectIdentity["color"] })}
                        >
                            <option value="">{t("project.identity.auto")}</option>
                            {Object.keys(projectIdentityColors).map((color) => (
                                <option key={color} value={color}>
                                    {t(`project.identity.${color}`)}
                                </option>
                            ))}
                        </select>
                    </label>
                    {identityStyle === "icon" && (
                        <label className="grid gap-1 text-sm">
                            <span className="flex items-center gap-2">
                                {t("project.identity.icon")}
                                <IconComponent icon={displayIdentity?.icon ?? "folder-kanban"} size="4" aria-hidden="true" />
                            </span>
                            <select
                                className="h-9 rounded-md border bg-background px-2"
                                value={displayIdentity?.icon ?? "folder-kanban"}
                                onChange={(event) => changeIdentity({ icon: event.currentTarget.value as ProjectIdentity["icon"] })}
                            >
                                {projectIdentityIcons.map((icon) => (
                                    <option key={icon} value={icon}>
                                        {t(`project.identity.${icon}`)}
                                    </option>
                                ))}
                            </select>
                        </label>
                    )}
                    {identityStyle === "abbreviation" && (
                        <label className="grid gap-1 text-sm">
                            {t("project.identity.abbreviation")}
                            <Input
                                maxLength={4}
                                value={displayIdentity?.abbreviation ?? ""}
                                onChange={(event) => changeIdentity({ abbreviation: event.currentTarget.value })}
                            />
                        </label>
                    )}
                </fieldset>
                <Label display="inline-grid" items="center" gap="2" w="full">
                    <Box>{t("project.Project title")}</Box>
                    <Form.Field name="title">
                        <Collaborative.Input
                            name="title"
                            collaborationType={EEditorCollaborationType.BoardSettings}
                            uid={project.uid}
                            field="title"
                            defaultValue={title}
                            disabled={!isBasicInfoEditing}
                            readOnly={isValidating}
                        />
                    </Form.Field>
                </Label>
                {errors.title && <FormErrorMessage error={errors.title} icon="circle-alert" />}
                <Label display="inline-grid" items="center" gap="2" w="full">
                    <Box>{t("project.Project description")}</Box>
                    <Form.Field name="description">
                        <Collaborative.Textarea
                            name="description"
                            collaborationType={EEditorCollaborationType.BoardSettings}
                            uid={project.uid}
                            field="description"
                            defaultValue={description}
                            disabled={!isBasicInfoEditing}
                            readOnly={isValidating}
                            resize="none"
                            className="max-h-36 min-h-36"
                        />
                    </Form.Field>
                </Label>
                <Form.Field name="project_type">
                    <Label display="inline-grid" items="center" gap="2" w="full">
                        <Box>{t("project.Project type")}</Box>
                        <Input type="hidden" name="project_type" value={projectTypeRef.current} ref={projectTypeInputRef} />
                        <AutoComplete
                            selectedValue={collaborativeProjectType}
                            onValueChange={setProjectType}
                            collaborativeCursors={projectTypeRemoteCursors}
                            items={Project.TYPES.map((project_type) => ({
                                value: project_type,
                                label: t(project_type === "Other" ? "common.Other" : `project.types.${project_type}`),
                            }))}
                            emptyMessage={projectTypeRef.current ?? ""}
                            disabled={isValidating || !isBasicInfoEditing}
                            onSelectionChange={updateProjectTypeSelection}
                            placeholder={t("project.Project type")}
                        />
                    </Label>
                    {errors.project_type && <FormErrorMessage error={errors.project_type} icon="circle-alert" />}
                </Form.Field>
                <Label display="inline-grid" items="center" gap="2" w="full">
                    <Box>{t("project.Archive visible days")}</Box>
                    <Form.Field name="archive_visible_days">
                        <Collaborative.Input
                            type="number"
                            min="1"
                            step="1"
                            name="archive_visible_days"
                            collaborationType={EEditorCollaborationType.BoardSettings}
                            uid={project.uid}
                            field="archive_visible_days"
                            defaultValue={archiveVisibleDays ?? 3}
                            disabled={!isBasicInfoEditing}
                            readOnly={isValidating}
                        />
                        {errors.archive_visible_days && <FormErrorMessage error={errors.archive_visible_days} icon="circle-alert" />}
                    </Form.Field>
                </Label>
                <p className="text-xs text-muted-foreground">
                    {t("project.Archived cards are hidden from the default board after {{days}} days, not deleted.", {
                        days: archiveVisibleDays ?? 3,
                    })}{" "}
                    {t("project.Use View all archived cards to find older cards.")}
                </p>
                {aiDescription && (
                    <Box>
                        <Box textSize="sm" weight="semibold" className="select-none">
                            {t("project.AI summary")}
                        </Box>
                        <Textarea value={aiDescription} disabled className="min-h-36" />
                    </Box>
                )}
            </Form.Root>
        </Flex>
    );
});

export default BoardSettingsBasic;
