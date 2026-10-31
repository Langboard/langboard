import * as Slider from "@radix-ui/react-slider";
import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import { STEPPED_CHOICE_EFFECTS, type TSteppedChoiceEffect } from "./SteppedChoiceEffects";
import "./SteppedChoiceSlider.css";
import "./SteppedChoiceEffects.css";

export interface ISteppedChoice<T extends string = string> {
    id: T;
    label: string;
    hint: string;
    color: string;
    effect?: TSteppedChoiceEffect;
}

export interface ISteppedChoiceSliderProps<T extends string = string> {
    choices: readonly ISteppedChoice<T>[];
    value: T;
    onChange: (value: T) => void;
    onCommit: (value: T) => void;
    disabled?: boolean;
    ariaLabel: string;
}

export function SteppedChoiceSlider<T extends string>({
    choices,
    value,
    onChange,
    onCommit,
    disabled = false,
    ariaLabel,
}: ISteppedChoiceSliderProps<T>) {
    const hintId = useId();
    const panelRef = useRef<HTMLDivElement>(null);
    const [motionPaused, setMotionPaused] = useState(false);

    useEffect(() => {
        let visible = true;
        const update = () => setMotionPaused(!visible || document.hidden);
        const observer =
            typeof IntersectionObserver === "undefined"
                ? undefined
                : new IntersectionObserver(([entry]) => {
                      visible = entry.isIntersecting;
                      update();
                  });
        if (panelRef.current) observer?.observe(panelRef.current);
        document.addEventListener("visibilitychange", update);
        update();
        return () => {
            observer?.disconnect();
            document.removeEventListener("visibilitychange", update);
        };
    }, []);

    if (!choices.length) return null;
    const index = Math.max(
        0,
        choices.findIndex((choice) => choice.id === value)
    );
    const selected = choices[index];
    const effect = selected.effect ?? "none";
    const isDisabled = disabled || choices.length < 2;
    const choose = (values: number[], callback: (id: T) => void) => {
        const choice = choices[values[0]];
        if (!isDisabled && choice) callback(choice.id);
    };

    return (
        <div
            ref={panelRef}
            className={[
                "stepped-choice-panel rounded-xl border border-border bg-popover p-4 text-popover-foreground",
                STEPPED_CHOICE_EFFECTS[effect],
            ].join(" ")}
            data-effect={effect}
            data-motion-paused={motionPaused}
            data-disabled={isDisabled}
            style={{ "--stepped-choice-color": selected.color } as CSSProperties}
        >
            <div className="stepped-choice-current">
                <span className="stepped-choice-signal" aria-hidden="true" />
                <strong>{selected.label}</strong>
            </div>
            <p id={hintId} className="stepped-choice-hint text-muted-foreground">
                {selected.hint}
            </p>
            <Slider.Root
                className="stepped-choice-root"
                min={0}
                max={Math.max(1, choices.length - 1)}
                step={1}
                value={[index]}
                disabled={isDisabled}
                onValueChange={(values: number[]) => choose(values, onChange)}
                onValueCommit={(values: number[]) => choose(values, onCommit)}
            >
                <Slider.Track className="stepped-choice-track">
                    <Slider.Range className="stepped-choice-range" />
                    {choices.map((choice, position) => (
                        <span
                            key={choice.id}
                            className="stepped-choice-step"
                            aria-hidden="true"
                            data-selected={position <= index}
                            style={{ left: `${choices.length > 1 ? (100 * position) / (choices.length - 1) : 0}%` }}
                        />
                    ))}
                </Slider.Track>
                <Slider.Thumb className="stepped-choice-thumb" aria-label={ariaLabel} aria-valuetext={selected.label} aria-describedby={hintId} />
            </Slider.Root>
            <div className="stepped-choice-labels" aria-hidden="true">
                {choices.map((choice) => (
                    <span key={choice.id} data-current={choice.id === selected.id}>
                        {choice.label}
                    </span>
                ))}
            </div>
        </div>
    );
}

export default SteppedChoiceSlider;
