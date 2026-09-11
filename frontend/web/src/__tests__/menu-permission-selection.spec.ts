import { mount, flushPromises } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { describe, expect, it, vi } from "vitest";
import MenuTree from "@/components/others/fa-menu-tree-table/index.vue";
vi.mock("@/components/others/fa-menu-route-icon/index.vue", () => ({
  default: { template: "<span />" },
}));

describe("菜单授权不扩展到兄弟菜单", () => {
  it("仅勾选用户查询时只保存该按钮、用户菜单及祖先目录", async () => {
    const wrapper = mount(MenuTree, {
      props: {
        menuTree: [
          {
            id: 1,
            type: 1,
            name: "系统管理",
            children: [
              { id: 2, type: 2, name: "用户管理", children: [{ id: 3, type: 3, name: "查询" }] },
              { id: 4, type: 2, name: "角色管理", children: [{ id: 5, type: 3, name: "编辑" }] },
            ],
          },
        ],
        checkedIds: [],
      },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();
    const checkbox = wrapper.findAll("label").find((el) => el.text() === "查询")!;
    await checkbox.find("input").setValue(true);
    await flushPromises();
    expect(wrapper.vm.getCheckedIds().sort()).toEqual([1, 2, 3]);
    await wrapper.setProps({ checkedIds: [1, 2, 3] });
    await flushPromises();
    expect(wrapper.vm.getCheckedIds().sort()).toEqual([1, 2, 3]);
    wrapper.unmount();
  });
});
