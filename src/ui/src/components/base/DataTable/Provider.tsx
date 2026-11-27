import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useLocation } from "react-router";

export interface IDataTableContext {
    currentPage: number;
    columnFilters: Exclude<IPaginateOptions["columnFilters"], undefined>;
    sortConfig: Exclude<IPaginateOptions["sort"], undefined>;
    searchText: string;
    itemsPerPage: number;
    totalRecords: number;
    setTotalRecords: React.Dispatch<React.SetStateAction<number>>;
    paginate: (opts: IPaginateOptions) => void;
}

interface IDataTableProviderProps {
    itemsPerPage?: number;
    totalRecords: number;
    onPaginated?: () => void;
    children: React.ReactNode;
}

export interface IPaginateOptions {
    page?: number;
    columnFilters?: Record<string, string>;
    sort?: { key: string; direction: "asc" | "desc" };
    search?: string;
}

const initialContext = {
    currentPage: 1,
    columnFilters: {},
    sortConfig: { key: "", direction: "asc" as const },
    searchText: "",
    itemsPerPage: 15,
    totalRecords: 0,
    setTotalRecords: () => {},
    paginate: () => {},
};

const DataTableContext = createContext<IDataTableContext>(initialContext);

export const DataTableProvider = ({ itemsPerPage = 10, totalRecords, onPaginated, children }: IDataTableProviderProps): React.ReactNode => {
    const location = useLocation();
    const navigate = usePageNavigateRef();
    const [displayTotalRecords, setDisplayTotalRecords] = useState(totalRecords);
    const [state, setState] = useState<{
        currentPage: number;
        columnFilters: Exclude<IPaginateOptions["columnFilters"], undefined>;
        sortConfig: Exclude<IPaginateOptions["sort"], undefined>;
        searchText: string;
    }>({
        currentPage: location.state?.page || 1,
        columnFilters: location.state?.columnFilters || {},
        sortConfig: location.state?.sort || {},
        searchText: location.state?.search || "",
    });

    const paginate = useCallback(
        (opts: IPaginateOptions) => {
            navigate(
                {
                    pathname: location.pathname,
                    search: location.search,
                    hash: location.hash,
                },
                {
                    state: {
                        page: opts.page || state.currentPage,
                        columnFilters: opts.columnFilters || state.columnFilters,
                        sort: opts.sort || state.sortConfig,
                        search: opts.search ?? state.searchText,
                    },
                }
            );

            onPaginated?.();
        },
        [state, location, onPaginated]
    );

    useEffect(() => {
        setDisplayTotalRecords(totalRecords);
    }, [totalRecords]);

    useEffect(() => {
        if (location.state) {
            setState({
                currentPage: location.state.page || 1,
                columnFilters: location.state.columnFilters || {},
                sortConfig: location.state.sort || {},
                searchText: location.state.search || "",
            });
        }
    }, [location, location.state]);

    return (
        <DataTableContext.Provider
            value={{
                currentPage: state.currentPage,
                columnFilters: state.columnFilters,
                sortConfig: state.sortConfig,
                searchText: state.searchText,
                itemsPerPage,
                totalRecords: displayTotalRecords,
                setTotalRecords: setDisplayTotalRecords,
                paginate,
            }}
        >
            {children}
        </DataTableContext.Provider>
    );
};

export const useDataTable = () => {
    const context = useContext(DataTableContext);
    if (!context) {
        throw new Error("useDataTable must be used within an DataTableProvider");
    }
    return context;
};
