package handler

import (
	"net/http"

	"github.com/Tencent/WeKnora/internal/types"
	"github.com/gin-gonic/gin"
)

// ListWorkbenches godoc
// @Summary      获取工作台清单
// @Description  返回后端注册的全部工作台（业务域）及其组件/工具白名单。前端据此构建「工作台 → 面板」映射，只保留组件实现而不保留词表。
// @Tags         系统
// @Produce      json
// @Success      200  {object}  map[string]interface{}  "标准 code/msg/data 包装，data 为 []types.WorkbenchDef"
// @Router       /system/workspaces [get]
func (h *SystemHandler) ListWorkbenches(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"code": 0,
		"msg":  "success",
		"data": types.ListWorkbenches(),
	})
}
