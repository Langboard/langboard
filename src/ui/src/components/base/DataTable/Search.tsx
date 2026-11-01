import Badge from "@/components/base/Badge";
import { useDataTable } from "@/components/base/DataTable/Provider";
import Input from "@/components/base/Input";
import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export interface IDataTableSearchProps {
    placeholder?: string;
}

function DataTableSearch({ placeholder }: IDataTableSearchProps) {
    const [t] = useTranslation();
    const { paginate, searchText, columnFilters } = useDataTable();
    const [inputValue, setInputValue] = useState(searchText);
    const lastInputValueRef = useRef(searchText);
    const throttleSearchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    useEffect(
        () => () => {
            if (throttleSearchTimeoutRef.current) clearTimeout(throttleSearchTimeoutRef.current);
        },
        []
    );
    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setInputValue(e.currentTarget.value);
        lastInputValueRef.current = e.currentTarget.value;
        if (throttleSearchTimeoutRef.current) clearTimeout(throttleSearchTimeoutRef.current);
        throttleSearchTimeoutRef.current = setTimeout(() => {
            paginate({ page: 1, search: lastInputValueRef.current });
        }, 500);
    };
    const handleClearSearchText = () => {
        if (throttleSearchTimeoutRef.current) clearTimeout(throttleSearchTimeoutRef.current);
        setInputValue("");
        lastInputValueRef.current = "";
        paginate({ page: 1, search: "" });
    };

    useEffect(() => {
        setInputValue(searchText);
        lastInputValueRef.current = searchText;
    }, [searchText]);

    return (
        <div className="flex flex-col items-start gap-4 p-6 pb-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap items-center gap-2">
                {Object.keys(columnFilters).length > 0 && (
                    <>
                        <span className="text-sm text-muted-foreground">{t("datatable.Active filters")}:</span>
                        {Object.entries(columnFilters).map(([columnKey, value]) => (
                            <DataTableFilteredColumn key={columnKey} columnKey={columnKey} value={value} />
                        ))}
                    </>
                )}
            </div>
            <div className="relative w-full sm:w-auto sm:max-w-sm sm:flex-1">
                <Input
                    placeholder={placeholder ?? t("datatable.Search...")}
                    value={inputValue}
                    onChange={handleInputChange}
                    leftIcon={<Search />}
                    clearable
                    onClear={handleClearSearchText}
                    className="w-full"
                />
            </div>
        </div>
    );
}

function DataTableFilteredColumn({ columnKey, value }: { columnKey: string; value: string }) {
    const { paginate, columnFilters } = useDataTable();
    const handleClearColumnFilter = () => {
        const newFilters = { ...columnFilters };
        delete newFilters[columnKey];
        paginate({
            page: 1,
            columnFilters: newFilters,
        });
    };

    return (
        <Badge key={columnKey} variant="secondary" className="cursor-pointer text-xs" onClick={handleClearColumnFilter}>
            {columnKey}: {value || ""} ×
        </Badge>
    );
}

export default DataTableSearch;
