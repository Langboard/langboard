import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { UserGroup } from "@/core/models";

export interface ICreateUserGroupForm {
    name: string;
}

export interface ICreateUserGroupResponse {
    user_group: UserGroup.TModel;
}

const useCreateUserGroup = (options?: TMutationOptions<ICreateUserGroupForm, ICreateUserGroupResponse>) => {
    const { mutate } = useQueryMutation();

    const createUserGroup = async (params: ICreateUserGroupForm) => {
        const res = await api.post<{ user_group: UserGroup.Interface }>(
            Routing.API.ACCOUNT.USER_GROUP.CREATE,
            {
                name: params.name,
            },
            {
                env: {
                    interceptToast: options?.interceptToast,
                } as never,
            }
        );

        return {
            user_group: UserGroup.Model.fromOne(res.data.user_group, true),
        };
    };

    const result = mutate<ICreateUserGroupForm, ICreateUserGroupResponse>(["create-user-group"], createUserGroup, {
        ...options,
        retry: 0,
    });

    return result;
};

export default useCreateUserGroup;
