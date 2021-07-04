#include <pybind11/eigen.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/iostream.h>
#include "gmmtree.h"

namespace py = pybind11;
using namespace probreg;

PYBIND11_MODULE(_gmmtree, m) {
    Eigen::initParallel();

    m.def("build_gmmtree", &buildGmmTree,
          py::call_guard<py::scoped_ostream_redirect,
                         py::scoped_estream_redirect>());
    m.def("gmmtree_reg_estep", gmmTreeRegEstep);

#ifdef VERSION_INFO
    m.attr("__version__") = VERSION_INFO;
#else
    m.attr("__version__") = "dev";
#endif
}
