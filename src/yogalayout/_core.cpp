// nanobind binding for Yoga's public C API (yoga/Yoga.h) with RAII ownership:
//   * Config owns its YGConfigRef; every Node keeps its Config alive.
//   * A parent keeps strong references to its children; a child only reaches its parent
//     weakly (through YGNodeGetParent + context), so trees contain no reference cycles.
//   * Every Yoga precondition that would abort the process is checked up front and raised
//     as a Python exception instead.
#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>

#include <cmath>
#include <cstdio>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <yoga/Yoga.h>

namespace nb = nanobind;
using namespace nb::literals;

namespace {

// ---------------------------------------------------------------- Config

struct Config {
  YGConfigRef ref;
  Config() : ref(YGConfigNew()) {}
  Config(const Config&) = delete;
  Config& operator=(const Config&) = delete;
  ~Config() { YGConfigFree(ref); }
};

// ---------------------------------------------------------------- Node

struct Node;
// Exception raised inside a callback; rethrown by calculate_layout once Yoga has returned.
std::unique_ptr<nb::python_error> g_pending;

struct Node {
  YGNodeRef ref;
  nb::object config;                 // keeps the owning Config alive (None => default config)
  std::vector<nb::object> children;  // mirrors Yoga's child list
  nb::object measure, baseline, dirtied;

  explicit Node(nb::object cfg) : config(std::move(cfg)) {
    if (config.is_none()) {
      ref = YGNodeNew();
    } else {
      ref = YGNodeNewWithConfig(nb::cast<Config&>(config).ref);
    }
    YGNodeSetContext(ref, this);
  }
  Node(const Node&) = delete;
  Node& operator=(const Node&) = delete;
  ~Node() {
    // Detach (not free) children first, then free ourselves; the vector then releases children.
    YGNodeRemoveAllChildren(ref);
    YGNodeFree(ref);
  }
};

Node* nodeOf(YGNodeConstRef n) { return static_cast<Node*>(YGNodeGetContext(n)); }

YGSize measureTrampoline(YGNodeConstRef n, float w, YGMeasureMode wm, float h, YGMeasureMode hm) {
  Node* node = nodeOf(n);
  if (g_pending) return {0, 0};
  try {
    nb::tuple r = nb::cast<nb::tuple>(node->measure(w, wm, h, hm));
    if (r.size() != 2) throw nb::value_error("measure function must return (width, height)");
    return {nb::cast<float>(r[0]), nb::cast<float>(r[1])};
  } catch (nb::python_error& e) {
    g_pending = std::make_unique<nb::python_error>(std::move(e));
  } catch (std::exception& e) {
    PyErr_SetString(PyExc_ValueError, e.what());
    g_pending = std::make_unique<nb::python_error>();
  }
  return {0, 0};
}

float baselineTrampoline(YGNodeConstRef n, float w, float h) {
  Node* node = nodeOf(n);
  if (g_pending) return 0;
  try {
    return nb::cast<float>(node->baseline(w, h));
  } catch (nb::python_error& e) {
    g_pending = std::make_unique<nb::python_error>(std::move(e));
  }
  return 0;
}

void dirtiedTrampoline(YGNodeConstRef n) {
  Node* node = nodeOf(n);
  try {
    node->dirtied(nb::find(node));
  } catch (nb::python_error& e) {
    if (!g_pending) g_pending = std::make_unique<nb::python_error>(std::move(e));
  }
}

// GC support: lets Python collect cycles through children/callbacks (e.g. measure closures).
int nodeTraverse(PyObject* self, visitproc visit, void* arg) {
#if PY_VERSION_HEX >= 0x03090000
  Py_VISIT(Py_TYPE(self));
#endif
  if (!nb::inst_ready(self)) return 0;
  Node* n = nb::inst_ptr<Node>(self);
  Py_VISIT(n->config.ptr());
  Py_VISIT(n->measure.ptr());
  Py_VISIT(n->baseline.ptr());
  Py_VISIT(n->dirtied.ptr());
  for (auto& c : n->children) Py_VISIT(c.ptr());
  return 0;
}

int nodeClear(PyObject* self) {
  if (!nb::inst_ready(self)) return 0;
  Node* n = nb::inst_ptr<Node>(self);
  YGNodeRemoveAllChildren(n->ref);
  n->children.clear();
  n->measure.reset();
  n->baseline.reset();
  n->dirtied.reset();
  YGNodeSetMeasureFunc(n->ref, nullptr);
  YGNodeSetBaselineFunc(n->ref, nullptr);
  YGNodeSetDirtiedFunc(n->ref, nullptr);
  return 0;
}

PyType_Slot nodeSlots[] = {
    {Py_tp_traverse, (void*)nodeTraverse},
    {Py_tp_clear, (void*)nodeClear},
    {0, nullptr},
};

// ---------------------------------------------------------------- Dimension values

// A style length: Python float/int => points, "50%" => percent, "auto", or None => undefined.
struct Length {
  enum Kind { Undefined, Point, Percent, Auto } kind = Undefined;
  float v = 0;
};

Length parseLength(nb::handle h) {
  if (h.is_none()) return {Length::Undefined, 0};
  if (nb::isinstance<nb::str>(h)) {
    std::string s = nb::cast<std::string>(h);
    if (s == "auto") return {Length::Auto, 0};
    if (!s.empty() && s.back() == '%') {
      try {
        return {Length::Percent, std::stof(s.substr(0, s.size() - 1))};
      } catch (...) {
      }
    }
    throw nb::value_error(("invalid length: '" + s + "' (use a number, 'N%', 'auto' or None)").c_str());
  }
  if (nb::isinstance<YGValue>(h)) {
    YGValue val = nb::cast<YGValue>(h);
    switch (val.unit) {
      case YGUnitPoint: return {Length::Point, val.value};
      case YGUnitPercent: return {Length::Percent, val.value};
      case YGUnitAuto: return {Length::Auto, 0};
      default: return {Length::Undefined, 0};
    }
  }
  return {Length::Point, nb::cast<float>(h)};
}

nb::object lengthToPy(YGValue v) {
  return nb::cast(v);
}

std::string fmt(float v) {
  char buf[32];
  snprintf(buf, sizeof buf, "%g", v);
  return buf;
}

void requireChildIndex(const Node& n, size_t index, bool inclusive) {
  size_t count = n.children.size();
  if (index > count || (!inclusive && index == count))
    throw nb::index_error("child index out of range");
}

bool isAncestorOrSelf(Node* maybe, Node* of) {
  for (YGNodeRef p = of->ref; p; p = YGNodeGetParent(p))
    if (nodeOf(p) == maybe) return true;
  return false;
}

}  // namespace

// YGValue exposed as Value(value, unit)
NB_MODULE(_core, m) {
  m.doc() = "Low-level nanobind bindings for Yoga (use the yogalayout package, not this module)";
  m.attr("YOGA_VERSION") = "3.2.1";
  m.attr("UNDEFINED") = std::nanf("");

  // ---- enums (values come straight from YGEnums.h)
  nb::enum_<YGDirection>(m, "Direction")
      .value("INHERIT", YGDirectionInherit).value("LTR", YGDirectionLTR).value("RTL", YGDirectionRTL);
  nb::enum_<YGFlexDirection>(m, "FlexDirection")
      .value("COLUMN", YGFlexDirectionColumn).value("COLUMN_REVERSE", YGFlexDirectionColumnReverse)
      .value("ROW", YGFlexDirectionRow).value("ROW_REVERSE", YGFlexDirectionRowReverse);
  nb::enum_<YGJustify>(m, "Justify")
      .value("FLEX_START", YGJustifyFlexStart).value("CENTER", YGJustifyCenter)
      .value("FLEX_END", YGJustifyFlexEnd).value("SPACE_BETWEEN", YGJustifySpaceBetween)
      .value("SPACE_AROUND", YGJustifySpaceAround).value("SPACE_EVENLY", YGJustifySpaceEvenly);
  nb::enum_<YGAlign>(m, "Align")
      .value("AUTO", YGAlignAuto).value("FLEX_START", YGAlignFlexStart)
      .value("CENTER", YGAlignCenter).value("FLEX_END", YGAlignFlexEnd)
      .value("STRETCH", YGAlignStretch).value("BASELINE", YGAlignBaseline)
      .value("SPACE_BETWEEN", YGAlignSpaceBetween).value("SPACE_AROUND", YGAlignSpaceAround)
      .value("SPACE_EVENLY", YGAlignSpaceEvenly);
  nb::enum_<YGWrap>(m, "Wrap")
      .value("NO_WRAP", YGWrapNoWrap).value("WRAP", YGWrapWrap).value("WRAP_REVERSE", YGWrapWrapReverse);
  nb::enum_<YGPositionType>(m, "PositionType")
      .value("STATIC", YGPositionTypeStatic).value("RELATIVE", YGPositionTypeRelative)
      .value("ABSOLUTE", YGPositionTypeAbsolute);
  nb::enum_<YGDisplay>(m, "Display")
      .value("FLEX", YGDisplayFlex).value("NONE", YGDisplayNone).value("CONTENTS", YGDisplayContents);
  nb::enum_<YGOverflow>(m, "Overflow")
      .value("VISIBLE", YGOverflowVisible).value("HIDDEN", YGOverflowHidden)
      .value("SCROLL", YGOverflowScroll);
  nb::enum_<YGBoxSizing>(m, "BoxSizing")
      .value("BORDER_BOX", YGBoxSizingBorderBox).value("CONTENT_BOX", YGBoxSizingContentBox);
  nb::enum_<YGEdge>(m, "Edge")
      .value("LEFT", YGEdgeLeft).value("TOP", YGEdgeTop).value("RIGHT", YGEdgeRight)
      .value("BOTTOM", YGEdgeBottom).value("START", YGEdgeStart).value("END", YGEdgeEnd)
      .value("HORIZONTAL", YGEdgeHorizontal).value("VERTICAL", YGEdgeVertical).value("ALL", YGEdgeAll);
  nb::enum_<YGGutter>(m, "Gutter")
      .value("COLUMN", YGGutterColumn).value("ROW", YGGutterRow).value("ALL", YGGutterAll);
  nb::enum_<YGUnit>(m, "Unit")
      .value("UNDEFINED", YGUnitUndefined).value("POINT", YGUnitPoint)
      .value("PERCENT", YGUnitPercent).value("AUTO", YGUnitAuto);
  nb::enum_<YGMeasureMode>(m, "MeasureMode")
      .value("UNDEFINED", YGMeasureModeUndefined).value("EXACTLY", YGMeasureModeExactly)
      .value("AT_MOST", YGMeasureModeAtMost);
  nb::enum_<YGErrata>(m, "Errata", nb::is_flag())
      .value("NONE", YGErrataNone).value("STRETCH_FLEX_BASIS", YGErrataStretchFlexBasis)
      .value("ABSOLUTE_POSITION_WITHOUT_INSETS_EXCLUDES_PADDING",
             YGErrataAbsolutePositionWithoutInsetsExcludesPadding)
      .value("ABSOLUTE_PERCENT_AGAINST_INNER_SIZE", YGErrataAbsolutePercentAgainstInnerSize)
      .value("CLASSIC", YGErrataClassic).value("ALL", YGErrataAll);
  nb::enum_<YGExperimentalFeature>(m, "ExperimentalFeature")
      .value("WEB_FLEX_BASIS", YGExperimentalFeatureWebFlexBasis);

  nb::class_<YGValue>(m, "Value")
      .def(nb::init<float, YGUnit>(), "value"_a, "unit"_a)
      .def_rw("value", &YGValue::value)
      .def_rw("unit", &YGValue::unit)
      .def("__eq__", [](const YGValue& a, nb::object o) {
        if (!nb::isinstance<YGValue>(o)) return false;
        const YGValue& b = nb::cast<const YGValue&>(o);
        return a.unit == b.unit && (a.unit == YGUnitAuto || a.unit == YGUnitUndefined || a.value == b.value);
      })
      .def("__repr__", [](const YGValue& v) {
        switch (v.unit) {
          case YGUnitPoint: return "Value(" + fmt(v.value) + ", POINT)";
          case YGUnitPercent: return "Value(" + fmt(v.value) + ", PERCENT)";
          case YGUnitAuto: return std::string("Value(AUTO)");
          default: return std::string("Value(UNDEFINED)");
        }
      });

  struct Layout { float left, top, width, height; };
  nb::class_<Layout>(m, "Layout")
      .def_ro("left", &Layout::left).def_ro("top", &Layout::top)
      .def_ro("width", &Layout::width).def_ro("height", &Layout::height)
      .def_prop_ro("right", [](const Layout& l) { return l.left + l.width; })
      .def_prop_ro("bottom", [](const Layout& l) { return l.top + l.height; })
      .def("__iter__", [](const Layout& l) {
        return nb::iter(nb::make_tuple(l.left, l.top, l.width, l.height));
      })
      .def("__eq__", [](const Layout& a, nb::object o) {
        if (!nb::isinstance<Layout>(o)) return false;
        const Layout& b = nb::cast<const Layout&>(o);
        return a.left == b.left && a.top == b.top && a.width == b.width && a.height == b.height;
      })
      .def("__repr__", [](const Layout& l) {
        return "Layout(left=" + fmt(l.left) + ", top=" + fmt(l.top) + ", width=" + fmt(l.width) +
               ", height=" + fmt(l.height) + ")";
      });

  // ---- Config
  nb::class_<Config>(m, "Config")
      .def(nb::init<>())
      .def_prop_rw("use_web_defaults",
                   [](Config& c) { return YGConfigGetUseWebDefaults(c.ref); },
                   [](Config& c, bool v) { YGConfigSetUseWebDefaults(c.ref, v); })
      .def_prop_rw("point_scale_factor",
                   [](Config& c) { return YGConfigGetPointScaleFactor(c.ref); },
                   [](Config& c, float v) { YGConfigSetPointScaleFactor(c.ref, v); })
      .def_prop_rw("errata",
                   [](Config& c) { return YGConfigGetErrata(c.ref); },
                   [](Config& c, YGErrata v) { YGConfigSetErrata(c.ref, v); })
      .def("set_experimental_feature",
           [](Config& c, YGExperimentalFeature f, bool on) { YGConfigSetExperimentalFeatureEnabled(c.ref, f, on); })
      .def("is_experimental_feature_enabled",
           [](Config& c, YGExperimentalFeature f) { return YGConfigIsExperimentalFeatureEnabled(c.ref, f); });

  // ---- Node
  auto node = nb::class_<Node>(m, "Node", nb::type_slots(nodeSlots), nb::is_weak_referenceable())
      .def(nb::init<nb::object>(), "config"_a = nb::none());

  // tree
  node.def_prop_ro("config", [](Node& n) { return n.config; })
      .def("insert_child", [](Node& self, nb::object child_obj, std::optional<size_t> index) {
        Node& child = nb::cast<Node&>(child_obj);
        size_t idx = index.value_or(self.children.size());
        requireChildIndex(self, idx, true);
        if (&child == &self || isAncestorOrSelf(&child, &self))
          throw nb::value_error("cannot insert a node into itself or one of its descendants");
        if (YGNodeGetOwner(child.ref) != nullptr)
          throw nb::value_error("node already has a parent; remove it first");
        if (YGNodeHasMeasureFunc(self.ref))
          throw nb::value_error("a node with a measure function cannot have children");
        YGNodeInsertChild(self.ref, child.ref, idx);
        self.children.insert(self.children.begin() + idx, std::move(child_obj));
      }, "child"_a, "index"_a = nb::none())
      .def("remove_child", [](Node& self, nb::object child_obj) {
        Node& child = nb::cast<Node&>(child_obj);
        for (size_t i = 0; i < self.children.size(); ++i) {
          if (self.children[i].ptr() == child_obj.ptr()) {
            YGNodeRemoveChild(self.ref, child.ref);
            self.children.erase(self.children.begin() + i);
            return;
          }
        }
        throw nb::value_error("node is not a child of this node");
      }, "child"_a)
      .def("remove_all_children", [](Node& self) {
        YGNodeRemoveAllChildren(self.ref);
        self.children.clear();
      })
      .def_prop_ro("children", [](Node& n) {
        nb::list l;
        for (auto& c : n.children) l.append(c);
        return nb::tuple(l);
      })
      .def("child", [](Node& n, ssize_t i) {
        ssize_t count = (ssize_t)n.children.size();
        if (i < 0) i += count;
        if (i < 0 || i >= count) throw nb::index_error("child index out of range");
        return n.children[i];
      }, "index"_a)
      .def("__len__", [](Node& n) { return n.children.size(); })
      .def_prop_ro("parent", [](Node& n) -> nb::object {
        YGNodeRef p = YGNodeGetParent(n.ref);
        if (!p) return nb::none();
        return nb::find(nodeOf(p));
      });

  // layout
  node.def("calculate_layout", [](Node& n, std::optional<float> w, std::optional<float> h, YGDirection d) {
        g_pending.reset();
        YGNodeCalculateLayout(n.ref, w.value_or(YGUndefined), h.value_or(YGUndefined), d);
        if (g_pending) {
          std::unique_ptr<nb::python_error> e = std::move(g_pending);
          throw std::move(*e);
        }
      }, "width"_a = nb::none(), "height"_a = nb::none(), "direction"_a = YGDirectionLTR)
      .def_prop_ro("layout", [](Node& n) {
        return Layout{YGNodeLayoutGetLeft(n.ref), YGNodeLayoutGetTop(n.ref),
                      YGNodeLayoutGetWidth(n.ref), YGNodeLayoutGetHeight(n.ref)};
      })
      .def_prop_ro("layout_direction", [](Node& n) { return YGNodeLayoutGetDirection(n.ref); })
      .def_prop_ro("had_overflow", [](Node& n) { return YGNodeLayoutGetHadOverflow(n.ref); })
      .def("layout_margin", [](Node& n, YGEdge e) { return YGNodeLayoutGetMargin(n.ref, e); }, "edge"_a)
      .def("layout_padding", [](Node& n, YGEdge e) { return YGNodeLayoutGetPadding(n.ref, e); }, "edge"_a)
      .def("layout_border", [](Node& n, YGEdge e) { return YGNodeLayoutGetBorder(n.ref, e); }, "edge"_a)
      .def_prop_rw("has_new_layout",
                   [](Node& n) { return YGNodeGetHasNewLayout(n.ref); },
                   [](Node& n, bool v) { YGNodeSetHasNewLayout(n.ref, v); })
      .def_prop_ro("is_dirty", [](Node& n) { return YGNodeIsDirty(n.ref); })
      .def("mark_dirty", [](Node& n) {
        if (!YGNodeHasMeasureFunc(n.ref))
          throw nb::value_error("only nodes with a measure function can be marked dirty");
        YGNodeMarkDirty(n.ref);
      });

  // callbacks
  node.def("set_measure", [](Node& n, nb::object fn) {
        if (fn.is_none()) {
          YGNodeSetMeasureFunc(n.ref, nullptr);
          n.measure.reset();
          return;
        }
        if (!PyCallable_Check(fn.ptr())) throw nb::type_error("measure must be callable or None");
        if (!n.children.empty()) throw nb::value_error("a node with children cannot have a measure function");
        n.measure = std::move(fn);
        YGNodeSetMeasureFunc(n.ref, measureTrampoline);
      }, "fn"_a.none())
      .def("set_baseline", [](Node& n, nb::object fn) {
        if (fn.is_none()) {
          YGNodeSetBaselineFunc(n.ref, nullptr);
          n.baseline.reset();
          return;
        }
        if (!PyCallable_Check(fn.ptr())) throw nb::type_error("baseline must be callable or None");
        n.baseline = std::move(fn);
        YGNodeSetBaselineFunc(n.ref, baselineTrampoline);
      }, "fn"_a.none())
      .def("set_dirtied", [](Node& n, nb::object fn) {
        if (fn.is_none()) {
          YGNodeSetDirtiedFunc(n.ref, nullptr);
          n.dirtied.reset();
          return;
        }
        if (!PyCallable_Check(fn.ptr())) throw nb::type_error("dirtied must be callable or None");
        n.dirtied = std::move(fn);
        YGNodeSetDirtiedFunc(n.ref, dirtiedTrampoline);
      }, "fn"_a.none());

  // ---- style: enum / float properties
#define PROP_ENUM(py, T, Get, Set) \
  node.def_prop_rw(py, [](Node& n) { return YGNodeStyleGet##Get(n.ref); }, \
                       [](Node& n, T v) { YGNodeStyleSet##Set(n.ref, v); })
  PROP_ENUM("direction", YGDirection, Direction, Direction);
  PROP_ENUM("flex_direction", YGFlexDirection, FlexDirection, FlexDirection);
  PROP_ENUM("justify_content", YGJustify, JustifyContent, JustifyContent);
  PROP_ENUM("align_content", YGAlign, AlignContent, AlignContent);
  PROP_ENUM("align_items", YGAlign, AlignItems, AlignItems);
  PROP_ENUM("align_self", YGAlign, AlignSelf, AlignSelf);
  PROP_ENUM("position_type", YGPositionType, PositionType, PositionType);
  PROP_ENUM("flex_wrap", YGWrap, FlexWrap, FlexWrap);
  PROP_ENUM("overflow", YGOverflow, Overflow, Overflow);
  PROP_ENUM("display", YGDisplay, Display, Display);
  PROP_ENUM("box_sizing", YGBoxSizing, BoxSizing, BoxSizing);
#define PROP_FLOAT(py, Name) \
  node.def_prop_rw(py, [](Node& n) { return YGNodeStyleGet##Name(n.ref); }, \
                       [](Node& n, float v) { YGNodeStyleSet##Name(n.ref, v); })
  PROP_FLOAT("flex", Flex);
  PROP_FLOAT("flex_grow", FlexGrow);
  PROP_FLOAT("flex_shrink", FlexShrink);
  PROP_FLOAT("aspect_ratio", AspectRatio);

  // ---- style: lengths (width, height, min/max, flex_basis)
#define PROP_LENGTH(py, Name, AUTO_STMT)                                                 \
  node.def_prop_rw(py, [](Node& n) { return YGNodeStyleGet##Name(n.ref); },               \
                   [](Node& n, nb::object v) {                                             \
                     Length l = parseLength(v);                                            \
                     switch (l.kind) {                                                     \
                       case Length::Point: YGNodeStyleSet##Name(n.ref, l.v); break;        \
                       case Length::Percent: YGNodeStyleSet##Name##Percent(n.ref, l.v); break; \
                       case Length::Auto:                                                  \
                         AUTO_STMT;                                                        \
                         break;                                                            \
                       case Length::Undefined: YGNodeStyleSet##Name(n.ref, YGUndefined); break; \
                     }                                                                     \
                   }, nb::arg().none())
  PROP_LENGTH("width", Width, YGNodeStyleSetWidthAuto(n.ref));
  PROP_LENGTH("height", Height, YGNodeStyleSetHeightAuto(n.ref));
  PROP_LENGTH("flex_basis", FlexBasis, YGNodeStyleSetFlexBasisAuto(n.ref));
  PROP_LENGTH("min_width", MinWidth, throw nb::value_error("min_width does not accept 'auto'"));
  PROP_LENGTH("min_height", MinHeight, throw nb::value_error("min_height does not accept 'auto'"));
  PROP_LENGTH("max_width", MaxWidth, throw nb::value_error("max_width does not accept 'auto'"));
  PROP_LENGTH("max_height", MaxHeight, throw nb::value_error("max_height does not accept 'auto'"));

  // ---- style: per-edge / per-gutter
#define EDGE_LENGTH(py, Name, AUTO_STMT)                                                     \
  node.def("get_" py, [](Node& n, YGEdge e) { return YGNodeStyleGet##Name(n.ref, e); }, "edge"_a) \
      .def("set_" py, [](Node& n, YGEdge e, nb::object v) {                            \
        Length l = parseLength(v);                                                        \
        switch (l.kind) {                                                                 \
          case Length::Point: YGNodeStyleSet##Name(n.ref, e, l.v); break;                 \
          case Length::Percent: YGNodeStyleSet##Name##Percent(n.ref, e, l.v); break;      \
          case Length::Auto:                                                              \
            AUTO_STMT;                                                                    \
            break;                                                                        \
          case Length::Undefined: YGNodeStyleSet##Name(n.ref, e, YGUndefined); break;     \
        }                                                                                 \
      }, "edge"_a, "value"_a.none())
  EDGE_LENGTH("margin", Margin, YGNodeStyleSetMarginAuto(n.ref, e));
  EDGE_LENGTH("padding", Padding, throw nb::value_error("padding does not accept 'auto'"));
  EDGE_LENGTH("position", Position, YGNodeStyleSetPositionAuto(n.ref, e));
  node.def("get_border", [](Node& n, YGEdge e) { return YGNodeStyleGetBorder(n.ref, e); }, "edge"_a)
      .def("set_border", [](Node& n, YGEdge e, float v) { YGNodeStyleSetBorder(n.ref, e, v); }, "edge"_a, "value"_a)
      .def("get_gap", [](Node& n, YGGutter g) { return YGNodeStyleGetGap(n.ref, g); }, "gutter"_a)
      .def("set_gap", [](Node& n, YGGutter g, nb::object v) {
        Length l = parseLength(v);
        switch (l.kind) {
          case Length::Point: YGNodeStyleSetGap(n.ref, g, l.v); break;
          case Length::Percent: YGNodeStyleSetGapPercent(n.ref, g, l.v); break;
          case Length::Auto: throw nb::value_error("gap does not accept 'auto'");
          case Length::Undefined: YGNodeStyleSetGap(n.ref, g, YGUndefined); break;
        }
      }, "gutter"_a, "value"_a.none());
}
