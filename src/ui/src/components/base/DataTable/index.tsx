/* eslint-disable @typescript-eslint/no-explicit-any */
"use client";

import { DataTableProvider, IRemoteDataTable } from "@/components/base/DataTable/Provider";
import DataTableLoading from "@/components/base/DataTable/Loading";
import DataTableTable, { TDataTableTableProps } from "@/components/base/DataTable/Table";
import DataTableSearch from "@/components/base/DataTable/Search";
import DataTablePagination from "@/components/base/DataTable/Pagination";

export type TDataTableProps<T> = TDataTableTableProps<T> & {
    totalRecords: number;
    remote?: IRemoteDataTable;
    className?: string;
    searchPlaceholder?: string;
    itemsPerPage?: number;
    loading?: bool;
    onPaginated?: () => void;
};

function DataTable<T extends Record<string, any>>(props: TDataTableProps<T>) {
    if (props.loading && !props.remote) {
        return <DataTableLoading {...props} />;
    }
    return (
        <DataTableProvider {...props}>
            {props.searchable && <DataTableSearch placeholder={props.searchPlaceholder} />}
            <DataTableTable {...props} />
            {!props.loading && props.showPagination && <DataTablePagination />}
        </DataTableProvider>
    );
}

export default DataTable;
